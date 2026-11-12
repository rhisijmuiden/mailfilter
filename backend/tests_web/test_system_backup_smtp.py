"""Info/updates, backups and e-mail sending. The API calls the real mailfilterctl
(ops/bin/mailfilterctl) against a fake server in a temp folder."""

from __future__ import annotations

import smtplib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "etc/mailfilter").mkdir(parents=True)
    (root / "etc/mailfilter/relay_networks.list").write_text("203.0.113.25\n")
    monkeypatch.setenv("MF_CTL_COMMAND", f"{sys.executable} {REPO}/ops/bin/mailfilterctl")
    monkeypatch.setenv("MF_ROOT", str(root))
    monkeypatch.setenv("MF_ETC", str(root / "etc/mailfilter"))
    monkeypatch.setenv("MF_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("MF_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MF_BACKUP_SKIP", "database,redis")
    monkeypatch.setenv("MF_JOBS_INLINE", "1")
    monkeypatch.setenv("MF_NO_RECONFIGURE", "1")
    return root


def test_info_for_everyone(customer):
    r = customer.get("/api/system/info")
    assert r.status_code == 200 and r.json()["version"]


def test_system_admin_only(customer, ctl):
    for path in ("/api/system/details", "/api/backups", "/api/settings/smtp", "/api/system/jobs"):
        assert customer.get(path).status_code == 403
    assert customer.post("/api/system/update", json={}).status_code == 403
    assert customer.post("/api/backups", json={"type": "full"}).status_code == 403


def test_details(admin, ctl):
    r = admin.get("/api/system/details")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == (REPO / "VERSION").read_text().strip()


def test_backup_create_list_restore(admin, ctl):
    r = admin.post("/api/backups", json={"type": "full"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["state"] == "done", job
    assert admin.get(f"/api/system/jobs/{job['id']}").json()["kind"] == "backup"
    assert "Full backup" in admin.get(f"/api/system/jobs/{job['id']}/log").json()["log"]

    listing = admin.get("/api/backups").json()
    assert len(listing["backups"]) == 1
    bid = listing["backups"][0]["id"]
    assert admin.post(f"/api/backups/{bid}/verify").json()["ok"] is True

    (ctl / "etc/mailfilter/relay_networks.list").write_text("changed\n")
    r = admin.post(f"/api/backups/{bid}/restore", json={"components": ["files"], "safety_backup": False})
    assert r.json()["state"] == "done", r.text
    assert (ctl / "etc/mailfilter/relay_networks.list").read_text() == "203.0.113.25\n"


def test_backup_config_and_validation(admin, ctl):
    r = admin.put("/api/backups/config", json={"enabled": True, "time": "03:15", "full_every_days": 14, "keep_full": 3})
    assert r.status_code == 200, r.text
    assert admin.get("/api/backups").json()["config"]["time"] == "03:15"
    assert admin.put("/api/backups/config", json={"enabled": True, "time": "3pm", "full_every_days": 1, "keep_full": 1}).status_code == 422


def test_bad_ids_rejected(admin, ctl):
    assert admin.post("/api/backups/..%2Fetc/restore", json={}).status_code in (404, 422)
    assert admin.post("/api/backups/20261111T000000Z.999/restore", json={}).json()["detail"].startswith("ctl_error:")
    assert admin.get("/api/system/jobs/nope").status_code == 422


def test_ctl_missing(admin, monkeypatch):
    monkeypatch.setenv("MF_CTL_COMMAND", "/nonexistent/mailfilterctl")
    assert admin.get("/api/system/details").json()["detail"] == "ctl_unavailable"


class FakeSMTP:
    sent: list = []
    logins: list = []
    fail_auth = False

    def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return True

    def starttls(self, context=None):
        assert context.minimum_version.name == "TLSv1_2"

    def login(self, user, password):
        if FakeSMTP.fail_auth:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
        FakeSMTP.logins.append((user, password))

    def send_message(self, msg, from_addr, to_addrs):
        FakeSMTP.sent.append((self.host, self.port, msg, from_addr, to_addrs))

    def close(self):
        pass


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail_auth = [], [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_smtp_defaults_to_local(admin):
    r = admin.get("/api/settings/smtp").json()
    assert r["mode"] == "local" and r["has_password"] is False


def test_smtp_save_keeps_password_secret(admin, fake_smtp):
    body = {"mode": "smtp", "host": "smtp.example.org", "port": 587, "security": "starttls",
            "username": "mf@example.org", "password": "s3cret!", "from_address": "noreply@rhis.nl"}
    r = admin.put("/api/settings/smtp", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["has_password"] is True and "password" not in r.json()
    # Saving again without a password keeps it
    body.pop("password")
    admin.put("/api/settings/smtp", json=body)
    r = admin.post("/api/settings/smtp/test", json={**body, "to": "reinder@rhis.nl"})
    assert r.status_code == 200, r.text
    assert fake_smtp.logins == [("mf@example.org", "s3cret!")]
    host, port, msg, sender, to = fake_smtp.sent[0]
    assert (host, port, sender, to) == ("smtp.example.org", 587, "noreply@rhis.nl", ["reinder@rhis.nl"])
    assert msg["Auto-Submitted"] == "auto-generated" and msg["Message-ID"]


def test_smtp_local_mode_uses_postfix(admin, fake_smtp):
    r = admin.post("/api/settings/smtp/test", json={"mode": "local", "to": "reinder@rhis.nl"})
    assert r.status_code == 200, r.text
    assert fake_smtp.sent[0][:2] == ("127.0.0.1", 25) and fake_smtp.logins == []


def test_smtp_errors_are_explained(admin, fake_smtp):
    fake_smtp.fail_auth = True
    r = admin.post("/api/settings/smtp/test", json={"mode": "smtp", "host": "smtp.example.org", "username": "x",
                                                     "password": "bad", "to": "reinder@rhis.nl"})
    assert r.status_code == 400 and r.json()["detail"].startswith("smtp_auth_failed:")
    assert admin.post("/api/settings/smtp/test", json={"mode": "smtp", "to": "a@b.nl"}).json()["detail"] == "smtp_no_host"


def test_remote_backup_settings_sync_and_restore(admin, customer, ctl, tmp_path, monkeypatch):
    # "file" is a test-only protocol, so set it with the helper; the API covers the rest
    import json as _json
    import subprocess as _sp
    monkeypatch.setenv("MF_REMOTE_ALLOW_FILE", "1")
    assert customer.get("/api/backups/remote").status_code == 403

    r = admin.put("/api/backups/remote", json={"enabled": False, "protocol": "ftps", "host": "backup.example.org",
                                              "username": "mf", "password": "pw", "keep_full": 3})
    assert r.status_code == 200, r.text
    shown = r.json()
    assert shown["port"] == 21 and shown["has_password"] and shown["has_passphrase"] and "password" not in shown
    assert len(admin.get("/api/backups/remote/passphrase").json()["passphrase"]) >= 24
    r = admin.put("/api/backups/remote", json={"enabled": True, "protocol": "scp", "host": "bad host", "username": "mf"})
    assert r.json()["detail"].startswith("ctl_error:"), r.text

    target = tmp_path / "remote"
    _sp.run([sys.executable, f"{REPO}/ops/bin/mailfilterctl", "remote", "set", "--stdin"], check=True,
            input=_json.dumps({"enabled": True, "protocol": "file", "path": str(target)}), text=True)
    assert admin.post("/api/backups/remote/test").json()["ok"] is True

    job = admin.post("/api/backups", json={"type": "full"}).json()
    assert job["state"] == "done" and job["result"]["remote"]["ok"], job
    remote_list = admin.get("/api/backups/remote/backups").json()["backups"]
    assert len(remote_list) == 1 and remote_list[0]["encrypted"]
    assert admin.post("/api/backups/remote/sync").json()["state"] == "done"

    import shutil
    shutil.rmtree(tmp_path / "backups")
    (ctl / "etc/mailfilter/relay_networks.list").write_text("changed\n")
    bid = remote_list[0]["id"]
    r = admin.post(f"/api/backups/{bid}/restore", json={"components": ["files"], "safety_backup": False, "remote": True})
    assert r.json()["state"] == "done", r.text
    assert (ctl / "etc/mailfilter/relay_networks.list").read_text() == "203.0.113.25\n"
    names = [e["event"] for e in admin.get("/api/system/events").json()["events"]]
    assert names[0] == "restore.succeeded" and "remote.succeeded" in names
