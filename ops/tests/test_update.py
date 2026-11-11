import json
import subprocess

import pytest

from mailfilterctl import cli, system
from mailfilterctl.util import CtlError


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, version, msg, installable=True):
    (repo / "VERSION").write_text(version + "\n")
    if installable:
        (repo / "ops/bin").mkdir(parents=True, exist_ok=True)
        (repo / "ops/bin/mailfilterctl").write_text("#!/bin/sh\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", msg)


@pytest.fixture
def repos(env, tmp_path, monkeypatch):
    """A 'GitHub' bare repo with main and test, and a server checkout on test."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    commit(work, "0.0.1", "old main without updater", installable=False)
    git(work, "checkout", "-q", "-b", "test")
    commit(work, "0.1.0", "first test")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(origin)], check=True)
    server = tmp_path / "server"
    subprocess.run(["git", "clone", "-q", "-b", "test", str(origin), str(server)], check=True)
    monkeypatch.setenv("MF_SOURCE", str(server))
    git(work, "remote", "add", "origin", str(origin))
    return work, server


def test_check_finds_new_commits(repos):
    work, _ = repos
    assert system.check()["update_available"] is False
    commit(work, "0.1.1", "fix trace search")
    git(work, "push", "-q", "origin", "test")
    r = system.check()
    assert r["update_available"] and r["behind"] == 1
    assert r["commits"][0]["subject"] == "fix trace search"
    assert r["branches"]["test"]["version"] == "0.1.1"
    assert r["branches"]["main"]["installable"] is False


def test_apply_updates_and_records_history(repos, capsys):
    work, server = repos
    commit(work, "0.1.1", "fix")
    git(work, "push", "-q", "origin", "test")
    assert cli.main(["--json", "update", "apply"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["state"] == "done", job
    assert (server / "VERSION").read_text().strip() == "0.1.1"
    # a backup was made first
    assert any("before update" in b["label"] for b in json.loads(
        (cli.main(["--json", "backup", "list"]), capsys.readouterr().out)[1])["backups"])
    assert system.info()["last_update"]["ok"] is True


def test_apply_when_up_to_date(repos):
    assert system.apply(log=lambda *_: None)["changed"] is False


def test_refuses_branch_without_updater(repos):
    with pytest.raises(CtlError, match="no release"):
        system.apply("main", log=lambda *_: None)


def test_switch_branch(repos):
    work, server = repos
    git(work, "checkout", "-q", "main")
    git(work, "merge", "-q", "--ff-only", "test")
    git(work, "push", "-q", "origin", "main")
    r = system.apply("main", backup=False, log=lambda *_: None)
    assert r["to"]["branch"] == "main" and r["to"]["channel"] == "production"
    assert r["to"]["label"] == "0.1.0"


def test_failed_reconfigure_rolls_back(repos, monkeypatch):
    work, server = repos
    before = git(server, "rev-parse", "HEAD")
    commit(work, "0.2.0", "broken")
    git(work, "push", "-q", "origin", "test")
    calls = []

    def fake(log):
        calls.append(git(server, "rev-parse", "HEAD"))
        if len(calls) == 1:
            raise CtlError("postfix check failed")

    monkeypatch.setattr(system, "reconfigure", fake)
    with pytest.raises(CtlError, match="rolled back"):
        system.apply(backup=False, log=lambda *_: None)
    assert git(server, "rev-parse", "HEAD") == before
    assert calls[1] == before
    assert system.info()["last_update"]["ok"] is False


def test_rejects_unknown_branch(repos):
    with pytest.raises(CtlError):
        system.apply("evil; rm -rf /", log=lambda *_: None)


def test_only_one_job_at_a_time(repos, monkeypatch):
    from mailfilterctl import jobs
    monkeypatch.setenv("MF_JOBS_INLINE", "0")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: None)
    jobs.start("backup", {"type": "full"})
    with pytest.raises(CtlError, match="still running"):
        jobs.start("backup", {"type": "full"})


def test_version_file_is_semver():
    from pathlib import Path
    assert system.validate_version_file((Path(__file__).resolve().parents[2] / "VERSION").read_text())
