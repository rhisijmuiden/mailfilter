"""PMG import API: upload, preview, apply through the (faked) root job, delete."""

import json
import os
import sys

import pytest

from mailfilter import gateway
from mailfilter.db import session
from mailfilter.models import AddressRoute, DomainRoute, RelayNetwork
from mailfilter_pmg.tests import fixtures

BACKUP = fixtures.backup_tgz()


@pytest.fixture(autouse=True)
def staging(tmp_path, monkeypatch):
    sdir = tmp_path / "staging"
    root = tmp_path / "root"
    monkeypatch.setenv("MF_PMG_STAGING", str(sdir))
    # Stand-in for "sudo systemctl start mailfilter-pmg-import.service": the same
    # run_pending, below a temporary root.
    job = tmp_path / "job.py"
    job.write_text(
        "import sys\n"
        "from mailfilter_pmg.staging import run_pending\n"
        f"run_pending({str(sdir)!r}, root={str(root)!r})\n"
    )
    monkeypatch.setenv("MF_PMG_IMPORT_CMD", f"{sys.executable} {job}")
    # Stand-in for the gateway's root helper (sudo mailfilter-config apply --stdin)
    calls = []
    result = {"ok": True, "changed": [], "warnings": []}

    def fake(config_json):
        calls.append(json.loads(config_json))
        return result

    monkeypatch.setattr(gateway, "run_helper", fake)
    return {"dir": sdir, "root": root, "calls": calls, "result": result}


def upload(client, data=BACKUP):
    return client.post("/api/pmg-import", content=data, headers={"Content-Type": "application/octet-stream"})


def test_upload_preview_apply(admin, staging):
    r = upload(admin)
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["source"]["version"] == "8.2.3"
    assert view["summary"]["domains"] == 3
    keys = {(i["section"], i["key"]) for i in view["items"]}
    assert ("relay_networks", "2001:db8::/64") in keys
    assert any(r["name"] == "Disclaimer directie" and not r["handled_by"] for r in view["rules"])
    assert "BEGIN" not in json.dumps(view)  # DKIM key never leaves the server

    assert admin.get(f"/api/pmg-import/{view['id']}").json()["id"] == view["id"]

    sel = {"domains": ["shop.example"], "relay_networks": ["2001:db8::/64", "192.0.2.10/32"],
           "address_routes": ["info@special.example"], "lists": ["block:global:domain:spammer.example"]}
    r = admin.post(f"/api/pmg-import/{view['id']}/apply", json={"selection": sel})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ok"], res
    assert res["imported"]["domains"] == 1 and res["imported"]["lists"] == 1 and res["imported"]["relay_networks"] == 2
    # Gateway items are in the database and were applied through the gateway helper
    with session() as db:
        routes = {r.domain.name: r for r in db.query(DomainRoute)}
        assert [d.port for d in routes["shop.example"].destinations] == [2525]
        assert {n.cidr for n in db.query(RelayNetwork)} == {"2001:db8::/64", "192.0.2.10"}
        assert db.query(AddressRoute).one().protocol == "lmtp"
    sent = staging["calls"][-1]
    assert any(d["name"] == "shop.example" for d in sent["domains"])
    # Lists went through the root job, gateway.json was not touched by it
    root = staging["root"]
    assert "spammer.example" in (root / "etc/mailfilter/rspamd/block_domain.map").read_text()
    assert not (root / "etc/mailfilter/gateway.json").exists()

    # The preview now shows them as already there
    again = {(i["section"], i["key"]): i for i in admin.get(f"/api/pmg-import/{view['id']}").json()["items"]}
    assert again[("domains", "shop.example")]["status"] == "exists"
    assert again[("relay_networks", "192.0.2.10/32")]["status"] == "exists"

    assert admin.delete(f"/api/pmg-import/{view['id']}").status_code == 204
    assert os.listdir(staging["dir"]) == []
    assert admin.get(f"/api/pmg-import/{view['id']}").status_code == 404


def test_refused_by_server_changes_nothing(admin, staging):
    view = upload(admin).json()
    staging["result"].update({"ok": False, "step": "postfix check", "output": "fatal: broken"})
    res = admin.post(f"/api/pmg-import/{view['id']}/apply", json={"selection": {"domains": ["klant.nl"]}}).json()
    assert not res["ok"] and "broken" in res["error"]
    with session() as db:
        assert db.query(DomainRoute).count() == 0


def test_nothing_selected(admin):
    view = upload(admin).json()
    r = admin.post(f"/api/pmg-import/{view['id']}/apply", json={"selection": {"bogus": ["x"]}})
    assert r.status_code == 400
    assert r.json()["detail"] == "pmg_nothing_selected"


def test_bad_upload_is_rejected_and_removed(admin, staging):
    r = upload(admin, b"not a backup")
    assert r.status_code == 400
    assert r.json()["detail"].startswith("pmg_bad_backup:")
    assert os.listdir(staging["dir"]) == []
    assert upload(admin, b"").status_code == 400


def test_admin_only(client, users):
    client.login(users["customer"])
    assert upload(client).status_code == 403
    assert client.get("/api/pmg-import/" + "a" * 32).status_code == 403


def test_unknown_or_bad_id(admin):
    assert admin.get("/api/pmg-import/../../etc").status_code == 404
    assert admin.get("/api/pmg-import/" + "a" * 32).status_code == 404
