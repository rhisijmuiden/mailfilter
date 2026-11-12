"""Remote backups: upload after a backup, sync, retention, restore from the remote location.

FTP and FTPS run against pyftpdlib, SCP against a throwaway OpenSSH server.
They are skipped when those are not installed."""
import getpass
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from mailfilterctl import backup, cli, events, remote
from mailfilterctl.util import CtlError

QUIET = dict(log=lambda *_: None)


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def file_remote(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MF_REMOTE_ALLOW_FILE", "1")
    target = tmp_path / "remote"
    remote.set_config({"enabled": True, "protocol": "file", "path": str(target), "keep_full": 2})
    return target


def _roundtrip(env):
    """Full + incremental backup, wipe everything local, restore the incremental from the remote."""
    _write(env, "etc/mailfilter/relay_networks.list", "203.0.113.25\n")
    full = cli._job_backup(_FakeJob({"type": "full"}))
    assert full["remote"]["ok"], full["remote"]
    time.sleep(1.1)
    _write(env, "etc/mailfilter/relay_networks.list", "203.0.113.25\n2a02:58::/64\n")
    inc = cli._job_backup(_FakeJob({"type": "incremental"}))
    assert inc["remote"]["ok"] and inc["remote"]["uploaded"] == [inc["backup"]["id"]]

    listed = remote.list_remote()
    assert [b["id"] for b in listed] == [inc["backup"]["id"], full["backup"]["id"]]
    assert all(b["encrypted"] for b in listed)

    # New server: no local backups, settings (incl. passphrase) still known
    shutil.rmtree(backup.location())
    _write(env, "etc/mailfilter/relay_networks.list", "garbage\n")
    remote.restore_from_remote(inc["backup"]["id"], safety_backup=False, **QUIET)
    assert (env / "etc/mailfilter/relay_networks.list").read_text() == "203.0.113.25\n2a02:58::/64\n"
    assert {b["id"] for b in backup.list_backups()} == {inc["backup"]["id"], full["backup"]["id"]}
    return listed


class _FakeJob:
    def __init__(self, params):
        self.params = params
        self.steps = []

    def log(self, line):
        pass

    def step(self, text, key=None, **params):
        self.steps.append(key)


def test_file_remote_roundtrip_is_encrypted(env, file_remote):
    _roundtrip(env)
    files = [p for p in file_remote.rglob("*") if p.is_file()]
    assert any(p.name == "files.tar.zst.gpg" or p.name == "files.tar.gz.gpg" for p in files)
    # nothing but manifests is readable without the passphrase
    assert all(p.suffix == ".gpg" or p.name == "manifest.json" for p in files)


def test_wrong_passphrase_is_reported(env, file_remote):
    b = cli._job_backup(_FakeJob({"type": "full"}))["backup"]
    shutil.rmtree(backup.location())
    remote.set_config({"passphrase": "this is not the passphrase"})
    with pytest.raises(CtlError, match="passphrase"):
        remote.fetch(b["id"], **QUIET)
    assert not backup.list_backups()  # nothing half-downloaded left behind


def test_unencrypted_and_damaged_transfer(env, file_remote):
    remote.set_config({"encrypt": False})
    b = cli._job_backup(_FakeJob({"type": "full"}))["backup"]
    step = file_remote / b["chain"] / "000"
    assert (step / "manifest.json").exists() and not list(step.glob("*.gpg"))
    archive = next(p for p in step.iterdir() if p.name.startswith("files."))
    archive.write_bytes(archive.read_bytes() + b"x")
    shutil.rmtree(backup.location())
    with pytest.raises(CtlError, match="damaged"):
        remote.fetch(b["id"], **QUIET)


def test_sync_catches_up_and_prunes(env, file_remote):
    ids = []
    for _ in range(3):
        ids.append(backup.create("full", **QUIET)["backup"]["chain"])  # no upload: made outside a job
        time.sleep(1.05)
    r = remote.sync(**QUIET)
    # local retention is 4, remote keeps 2: only the newest two chains go up
    assert sorted(x.split(".")[0] for x in r["uploaded"]) == ids[1:]
    remote.set_config({"keep_full": 1})
    r = remote.sync(**QUIET)
    assert r["removed"] == [ids[1]]
    assert {b["chain"] for b in remote.list_remote()} == {ids[2]}
    assert remote.show()["status"]["ok"] is True


def test_failed_remote_does_not_fail_backup_and_emits_event(env, file_remote, tmp_path):
    remote.set_config({"path": str(tmp_path / "file-in-the-way" / "x")})
    (tmp_path / "file-in-the-way").write_text("not a folder")
    hooks = env / "etc/mailfilter/hooks.d"
    hooks.mkdir()
    out = tmp_path / "hook-out.json"
    hook = hooks / "10-test"
    hook.write_text(f"#!/bin/sh\ncat > {out}.$MF_EVENT\n")
    hook.chmod(0o755)

    os.environ["MF_JOBS_INLINE"] = "1"
    job = cli.dispatch(cli.build_parser().parse_args(["backup", "create", "--type", "full"]))
    assert job["state"] == "done"
    assert job["result"]["remote"]["ok"] is False
    assert "remote_failed" in [s["key"] for s in job["steps"]]
    names = [e["event"] for e in events.recent()]
    assert names[:2] == ["backup.succeeded", "remote.failed"]
    assert json.loads(Path(f"{out}.remote.failed").read_text())["backup"] == job["result"]["backup"]["id"]
    assert remote.show()["status"]["ok"] is False


def test_settings_validation_and_secrets(env, monkeypatch):
    with pytest.raises(CtlError):
        remote.set_config({"protocol": "http"})
    with pytest.raises(CtlError, match="server"):
        remote.set_config({"enabled": True, "protocol": "ftp", "host": "bad host", "username": "u"})
    with pytest.raises(CtlError, match="Folder"):
        remote.set_config({"protocol": "ftp", "host": "backup.example.org", "username": "u", "path": "../etc"})
    with pytest.raises(CtlError, match="12"):
        remote.set_config({"protocol": "ftp", "host": "backup.example.org", "username": "u", "passphrase": "short"})
    s = remote.set_config({"protocol": "ftps", "host": "backup.example.org", "port": "", "username": "u",
                           "password": "pw"})
    assert s["port"] == 21 and s["has_password"] and s["has_passphrase"]
    assert "password" not in s and "passphrase" not in s
    assert len(remote.show(reveal=True)["passphrase"]) >= 24
    assert oct(os.stat(remote._secret_path()).st_mode & 0o777) == "0o600"
    remote.set_config({"username": "v"})  # None/missing keeps the secrets
    assert remote.show()["has_password"]
    remote.set_config({"password": ""})
    assert not remote.show()["has_password"]


def test_cli_set_from_stdin(env, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"protocol": "ftp", "host": "h.example.org",
                                                             "username": "u", "password": "secret"})))
    assert cli.main(["--json", "remote", "set", "--stdin", "keep_full=3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["keep_full"] == 3 and out["has_password"] and "secret" not in json.dumps(out)
    assert cli.main(["--json", "remote", "set", "colour=red"]) == 1


def test_untrusted_hooks_are_skipped(env, tmp_path):
    hooks = env / "etc/mailfilter/hooks.d"
    hooks.mkdir()
    for name, mode in (("ok", 0o755), ("world-writable", 0o757), ("not-exec", 0o644), ("x.disabled", 0o755)):
        (hooks / name).write_text("#!/bin/sh\n")
        (hooks / name).chmod(mode)
    names = [p.name for p in events._hooks()]
    if os.geteuid() == 0:
        assert names == ["ok"]
    else:
        assert "not-exec" not in names and "x.disabled" not in names


# ---------------------------------------------------------------- FTP / FTPS

def _ftp_server(tmp_path, tls):
    pytest.importorskip("pyftpdlib")
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer

    home = tmp_path / "ftp-home"
    home.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("backup", "s3cret-pw", str(home), perm="elradfmwMT")
    if tls:
        pytest.importorskip("OpenSSL")
        cert = tmp_path / "ftp.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
                        "/CN=localhost", "-keyout", cert, "-out", cert], check=True, capture_output=True)

        class Handler(TLS_FTPHandler):
            pass
        Handler.certfile = str(cert)
        Handler.tls_control_required = True
        Handler.tls_data_required = True
    else:
        class Handler(FTPHandler):
            pass
    Handler.authorizer = auth
    Handler.passive_ports = None
    Handler.auth_failed_timeout = 0  # answer a wrong password at once
    server = ThreadedFTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.2}, daemon=True).start()
    return server, home


@pytest.mark.parametrize("protocol", ["ftp", "ftps"])
def test_ftp_and_ftps(env, tmp_path, protocol):
    server, home = _ftp_server(tmp_path, tls=protocol == "ftps")
    try:
        port = server.address[1]
        remote.set_config({"enabled": True, "protocol": protocol, "host": "127.0.0.1", "port": port,
                           "username": "backup", "password": "wrong", "path": "backups/mf",
                           "verify_tls": False})
        with pytest.raises(CtlError, match="refused the login"):
            remote.test(**QUIET)
        remote.set_config({"password": "s3cret-pw"})
        assert remote.test(**QUIET)["ok"]
        _roundtrip(env)
        assert list((home / "backups/mf").iterdir())
        assert not list(home.rglob("*.part"))
        if protocol == "ftps":
            # last: an aborted TLS handshake can leave this small test server in a bad state
            remote.set_config({"verify_tls": True})
            with pytest.raises(CtlError, match="certificate"):
                remote.test(**QUIET)
    finally:
        server.close_all()


# ---------------------------------------------------------------- SCP (SFTP)

@pytest.fixture
def sshd(tmp_path):
    if not (shutil.which("sshd") or Path("/usr/sbin/sshd").exists()) or not shutil.which("sftp"):
        pytest.skip("OpenSSH server/client not installed")
    sshd_bin = shutil.which("sshd") or "/usr/sbin/sshd"
    sftp_server = next((p for p in ("/usr/lib/openssh/sftp-server", "/usr/libexec/openssh/sftp-server",
                                    "/usr/libexec/sftp-server") if Path(p).exists()), None)
    if not sftp_server:
        pytest.skip("sftp-server not found")
    if os.geteuid() == 0:
        Path("/run/sshd").mkdir(exist_ok=True)  # privilege separation folder sshd expects as root
    d = tmp_path / "sshd"
    d.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", d / "host_key"], check=True)
    port = _free_port()
    auth = d / "authorized_keys"
    auth.write_text("")
    cfg = d / "sshd_config"
    cfg.write_text(f"""Port {port}
ListenAddress 127.0.0.1
HostKey {d / 'host_key'}
PidFile {d / 'sshd.pid'}
AuthorizedKeysFile {auth}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
UsePAM no
Subsystem sftp {sftp_server}
""")
    proc = subprocess.Popen([sshd_bin, "-D", "-e", "-f", cfg], stderr=subprocess.PIPE)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.skip("sshd did not start")
    yield port, auth, d / "host_key.pub", tmp_path / "sftp-target"
    proc.kill()


def test_scp_with_key_and_pinned_host_key(env, sshd):
    port, auth, host_pub, target = sshd
    s = remote.set_config({"enabled": True, "protocol": "scp", "host": "127.0.0.1", "port": port,
                           "username": getpass.getuser(), "path": str(target)})
    assert s["public_key"].startswith("ssh-ed25519 ")
    with pytest.raises(CtlError, match="refused the SSH key"):
        remote.test(**QUIET)
    learned = remote.show()["host_keys"]
    assert learned and "ED25519" in learned[0]
    auth.write_text(s["public_key"] + "\n")
    assert remote.test(**QUIET)["ok"]
    _roundtrip(env)
    assert (target).is_dir() and not list(target.rglob("*.part"))

    # A different host key on the same address is refused until it is forgotten
    kh = remote.ssh_dir() / "known_hosts"
    kh.write_text(kh.read_text().replace(host_pub.read_text().split()[1][:30], "AAAAC3NzaC1lZDI1NTE5AAAAIBBBBBBB"))
    with pytest.raises(CtlError):
        remote.test(**QUIET)
    remote.forget_host_key()
    assert remote.test(**QUIET)["ok"]
