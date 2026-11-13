"""mailfilterctl web: how the web interface is reached."""
import json

import pytest

from mailfilterctl import cli, web
from mailfilterctl.util import CtlError


@pytest.fixture
def server(env, tmp_path, monkeypatch):
    (env / "etc/mailfilter/mailfilter.env").write_text("MF_HOSTNAME=mf.example.nl\nMF_WEB_MODE=public\n")
    calls = tmp_path / "calls"
    script = tmp_path / "fake-web-access.sh"
    script.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    script.chmod(0o755)
    monkeypatch.setenv("MF_WEB_ACCESS_CMD", str(script))
    return calls


def test_show_defaults(server):
    s = web.show()
    assert s["mode"] == "public" and s["port"] == 443 and s["url"] == "https://mf.example.nl"


def test_validate():
    assert web.validate({"mode": "public"}) == {"mode": "public"}
    p = web.validate({"mode": "proxy", "trusted_proxies": "10.0.0.5 2001:db8::/64", "url": "https://mail.example.nl/"})
    assert p == {"mode": "proxy", "port": 8080, "trusted_proxies": ["10.0.0.5", "2001:db8::/64"], "url": "https://mail.example.nl"}
    assert web.validate({"mode": "http", "port": "8081", "web_from": ["192.168.1.0/24"]})["web_from"] == ["192.168.1.0/24"]
    for bad in ({"mode": "ftp"}, {"mode": "proxy"}, {"mode": "proxy", "trusted_proxies": "nope"},
                {"mode": "http", "port": 25}, {"mode": "proxy", "trusted_proxies": "10.0.0.5", "url": "javascript:x"}):
        with pytest.raises(CtlError):
            web.validate(bad)


def test_command_clears_lists():
    cmd = web.command({"mode": "internal", "web_from": []})
    assert cmd[-4:] == ["--mode", "internal", "--web-from", "-"]


def test_set_runs_web_access(server, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"mode": "proxy", "trusted_proxies": ["10.0.0.5"], "port": 8443})))
    assert cli.main(["--json", "web", "set", "--stdin"]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["state"] == "done"
    assert server.read_text().split() == ["--mode", "proxy", "--web-port", "8443", "--trusted-proxy", "10.0.0.5"]
    assert cli.main(["--json", "web", "set", "mode=nope"]) == 1
