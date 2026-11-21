"""mailfilterctl setup: the first-time wizard of the web interface."""
import json

import pytest

from mailfilterctl import cli, setup
from mailfilterctl.util import CtlError


@pytest.fixture
def server(env, tmp_path, monkeypatch):
    etc = env / "etc/mailfilter"
    (etc / "mailfilter.env").write_text("MF_HOSTNAME=mailfilter.localdomain\nMF_ADMIN_EMAIL=admin@example.nl\n"
                                        "MF_PUBLIC_V4=192.168.1.50\nMF_WEB_MODE=internal\nMF_WEB_URL=https://mailfilter.localdomain:8443\n")
    (etc / setup.MARKER).write_text("")
    calls = tmp_path / "calls"
    script = tmp_path / "fake-install.sh"
    script.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    script.chmod(0o755)
    monkeypatch.setenv("MF_SETUP_CMD", str(script))
    return calls


def test_show(server):
    s = setup.show()
    assert s["pending"] is True and s["hostname"] == "mailfilter.localdomain" and s["ipv4"] == "192.168.1.50"


def test_validate():
    assert setup.validate({"hostname": "MF01.Example.nl.", "admin_email": "Me@Example.nl"}) == {
        "hostname": "mf01.example.nl", "admin_email": "me@example.nl"}
    for bad in ({"hostname": "mailfilter", "admin_email": "a@b.nl"}, {"hostname": "mf.localdomain", "admin_email": "a@b.nl"},
                {"hostname": "mf.example.nl", "admin_email": "nope"}, {"hostname": "mf.example.nl;rm -rf", "admin_email": "a@b.nl"},
                {"hostname": "-x.example.nl", "admin_email": "a@b.nl"}):
        with pytest.raises(CtlError):
            setup.validate(bad)


def test_server_runs_installer(server, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"hostname": "mf01.example.nl", "admin_email": "it@example.nl"})))
    assert cli.main(["--json", "setup", "server", "--stdin"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["state"] == "done"
    assert server.read_text().split() == ["--reconfigure", "--hostname", "mf01.example.nl", "--admin-email", "it@example.nl"]
    assert cli.main(["--json", "setup", "server", "hostname=bad"]) == 1


def test_finish(server, capsys):
    assert cli.main(["--json", "setup", "finish"]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] is False
    assert cli.main(["--json", "setup", "finish"]) == 0
