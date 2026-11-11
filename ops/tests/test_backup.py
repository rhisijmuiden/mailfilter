import json
import time

import pytest

from mailfilterctl import backup, cli
from mailfilterctl.util import CtlError


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_full_incremental_and_point_in_time_restore(env):
    _write(env, "etc/mailfilter/relay_networks.list", "203.0.113.25\n")
    _write(env, "var/lib/rspamd/dkim/rhis.nl.mf2026.key", "KEY1")
    full = backup.create("full", log=lambda *_: None)["backup"]
    assert full["type"] == "full" and full["id"].endswith(".000")

    time.sleep(1.1)  # tar compares modification times in whole seconds
    _write(env, "etc/mailfilter/relay_networks.list", "203.0.113.25\n2a02:58::/64\n")
    _write(env, "etc/mailfilter/new.conf", "new")
    (env / "var/lib/rspamd/dkim/rhis.nl.mf2026.key").unlink()
    inc = backup.create("incremental", log=lambda *_: None)["backup"]
    assert inc["type"] == "incremental" and inc["id"].endswith(".001")
    assert inc["size"] < full["size"] * 2

    # Break things, then restore the full backup: the new file must disappear again
    _write(env, "etc/mailfilter/relay_networks.list", "garbage\n")
    backup.restore(full["id"], safety_backup=False, log=lambda *_: None)
    assert (env / "etc/mailfilter/relay_networks.list").read_text() == "203.0.113.25\n"
    assert not (env / "etc/mailfilter/new.conf").exists()
    assert (env / "var/lib/rspamd/dkim/rhis.nl.mf2026.key").read_text() == "KEY1"

    # Restore the incremental: replays full + incremental, including the deletion
    backup.restore(inc["id"], safety_backup=False, log=lambda *_: None)
    assert (env / "etc/mailfilter/relay_networks.list").read_text() == "203.0.113.25\n2a02:58::/64\n"
    assert (env / "etc/mailfilter/new.conf").read_text() == "new"
    assert not (env / "var/lib/rspamd/dkim/rhis.nl.mf2026.key").exists()


def test_auto_chooses_full_then_incremental(env):
    assert backup.choose_type() == "full"
    backup.create("auto", log=lambda *_: None)
    assert backup.choose_type() == "incremental"
    assert backup.create("auto", log=lambda *_: None)["backup"]["type"] == "incremental"


def test_incremental_without_full_makes_full(env):
    assert backup.create("incremental", log=lambda *_: None)["backup"]["type"] == "full"


def test_retention_keeps_newest_chains(env):
    backup.set_config({"keep_full": 2})
    for _ in range(3):
        backup.create("full", log=lambda *_: None)
        time.sleep(1.05)
    chains = {b["chain"] for b in backup.list_backups()}
    assert len(chains) == 2


def test_verify_detects_damage(env):
    _write(env, "etc/mailfilter/a", "a")
    b = backup.create("full", log=lambda *_: None)["backup"]
    assert backup.verify(b["id"], log=lambda *_: None)["ok"]
    archive = backup._archive(backup.location() / b["chain"] / "000")
    archive.write_bytes(archive.read_bytes()[:-10] + b"0123456789")
    with pytest.raises(CtlError, match="damaged"):
        backup.verify(b["id"], log=lambda *_: None)


def test_restore_makes_safety_backup(env):
    _write(env, "etc/mailfilter/a", "a")
    b = backup.create("full", log=lambda *_: None)["backup"]
    time.sleep(1.05)
    backup.restore(b["id"], log=lambda *_: None)
    labels = [x["label"] for x in backup.list_backups()]
    assert "before restore" in labels


@pytest.mark.parametrize("bad", ["../etc", "x", "20261111T000000Z.1", "20261111T000000Z.000/../../x"])
def test_rejects_bad_ids(env, bad):
    with pytest.raises(CtlError):
        backup.restore(bad, log=lambda *_: None)


@pytest.mark.parametrize("changes", [{"time": "25:00"}, {"location": "/"}, {"location": "rel"},
                                     {"keep_full": 0}, {"nope": 1}])
def test_config_validation(env, changes):
    with pytest.raises(CtlError):
        backup.set_config(changes)


def test_cli_backup_job_and_list(env, capsys):
    assert cli.main(["--json", "backup", "create", "--type", "full"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["state"] == "done" and job["result"]["backup"]["type"] == "full"
    assert cli.main(["--json", "backup", "list"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert len(listing["backups"]) == 1
    assert cli.main(["--json", "job", "status", job["id"]]) == 0
    capsys.readouterr()
    assert cli.main(["--json", "restore", "nonsense"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
