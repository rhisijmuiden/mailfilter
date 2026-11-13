"""System > Modules: optional parts switched on and off."""
import io
import json
import sys

import pytest
from fastapi.testclient import TestClient

from mailfilter import apikeys, cli, gateway, modules
from mailfilter.db import session
from mailfilter.dmarc import ingest as dmarc_ingest
from mailfilter.feedback import ingest as feedback_ingest
from mailfilter.main import app
from mailfilter.models import ArchiveDomain, AuditLog, CloudTenant, Domain
from mailfilter.settings_store import set_setting


@pytest.fixture(autouse=True)
def calls(monkeypatch):
    out = []

    def fake(config_json):
        out.append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", fake)
    apikeys.rate_limiter._hits.clear()
    return out


def switch(client, key, on):
    r = client.put(f"/api/modules/{key}", json={"enabled": on})
    assert r.status_code == 200, r.text
    return r.json()


def test_defaults_and_roles(admin, customer):
    d = admin.get("/api/modules").json()
    assert d["enabled"]["usage"] is True and d["enabled"]["mailtrap"] is False
    assert {m["key"] for m in d["modules"]} == {m.key for m in modules.MODULES}
    c = customer.get("/api/modules").json()
    assert c["modules"] is None and c["enabled"] == d["enabled"]
    assert customer.put("/api/modules/usage", json={"enabled": False}).status_code == 403
    assert admin.put("/api/modules/nope", json={"enabled": False}).status_code == 404


def test_off_hides_api_and_is_audited(admin):
    assert admin.get("/api/usage").status_code == 200
    d = switch(admin, "usage", False)
    assert d["enabled"]["usage"] is False
    r = admin.get("/api/usage")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled"
    assert admin.get("/api/billing").status_code == 403
    for key, path in (("dmarc", "/api/dmarc/overview"), ("spam_reports", "/api/spam-reports"),
                      ("monthly_report", "/api/monthly-report/settings"), ("cloud", "/api/cloud"),
                      ("link_protection", "/api/links"), ("quarantine_report", "/api/quarantine-report/settings"),
                      ("pmg_import", "/api/pmg-import/1")):
        switch(admin, key, False)
        assert admin.get(path).json()["detail"]["code"] == "module_disabled", path
    switch(admin, "usage", True)
    assert admin.get("/api/usage").status_code == 200
    with session() as db:
        rows = db.query(AuditLog).filter(AuditLog.action == "module_switched", AuditLog.target == "usage").all()
        assert [r.detail["enabled"] for r in rows] == [False, True]


def test_api_keys_refused_while_off(admin):
    key = admin.post("/api/api-keys", json={"name": "panel", "scopes": {"domains": "read"}}).json()["key"]
    panel = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    assert panel.get("/api/v1/me").status_code == 200
    switch(admin, "api", False)
    r = panel.get("/api/v1/me")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "module_disabled"
    assert admin.get("/api/api-keys").status_code == 403


def test_gateway_modules(admin, calls):
    with session() as db:
        dom = db.query(Domain).filter_by(name="klant.nl").one()
        db.add(CloudTenant(domain_id=dom.id, provider="m365", tenant_id="8c1f2b6e-0d3a-4c55-9e1b-6f0a2d7be42a"))
        db.add(ArchiveDomain(name="klant.nl", enabled=True))
        set_setting(db, gateway.RULES_KEY, {"link_protection": {"enabled": True, "options": {}, "domains": {}}})
        db.commit()
    mods = {m["key"]: m for m in admin.get("/api/modules").json()["modules"]}
    assert mods["cloud"]["in_use"] == 1 and mods["archive"]["in_use"] == 1 and mods["link_protection"]["in_use"] == 1

    switch(admin, "cloud", False)
    assert calls[-1]["cloud_tenants"] == []
    switch(admin, "archive", False)
    assert calls[-1]["archive_domains"] == []
    switch(admin, "link_protection", False)
    assert "link_protection" not in calls[-1]["custom_rules"]
    assert all(r["id"] != "link_protection" for r in admin.get("/api/rules").json()["rules"])
    switch(admin, "archive", True)
    assert calls[-1]["archive_domains"] == ["klant.nl"]
    n = len(calls)
    switch(admin, "archive", True)  # no change: nothing applied
    assert len(calls) == n


def test_mailtrap_uses_its_own_switch(admin, calls):
    switch(admin, "mailtrap", True)
    assert calls[-1]["mailtrap_enabled"] is True
    assert admin.get("/api/mailtrap").json()["enabled"] is True
    admin.put("/api/mailtrap/settings", json={"enabled": False})
    assert admin.get("/api/modules").json()["enabled"]["mailtrap"] is False


def test_refused_gateway_keeps_module_on(admin, monkeypatch):
    monkeypatch.setattr(gateway, "run_helper", lambda c: {"ok": False, "step": "postfix", "output": "boom"})
    r = admin.put("/api/modules/archive", json={"enabled": False})
    assert r.status_code == 502
    assert admin.get("/api/modules").json()["enabled"]["archive"] is True


def test_cluster_member_cannot_switch_cluster_off(admin):
    from mailfilter.cluster import state

    with session() as db:
        set_setting(db, state.CONFIG_KEY, {"self": "a"})
        db.commit()
    r = admin.put("/api/modules/cluster", json={"enabled": False})
    assert r.status_code == 409 and r.json()["detail"] == "cluster_member"


def test_background_work_stops(admin, monkeypatch, capsys):
    switch(admin, "dmarc", False)
    switch(admin, "spam_reports", False)
    switch(admin, "quarantine_report", False)
    switch(admin, "monthly_report", False)
    switch(admin, "usage", False)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(b"Subject: x\n\nhi")})())
    assert dmarc_ingest.main(["x@example.com"]) == 0
    assert "module is off" in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(b"Subject: x\n\nhi")})())
    assert feedback_ingest.main(["--to=spam"]) == 0
    assert "module is off" in capsys.readouterr().out

    def boom(*a, **k):
        raise AssertionError("should not run")

    monkeypatch.setattr("mailfilter.usage.run", boom)
    monkeypatch.setattr("mailfilter.monthly.run", boom)
    monkeypatch.setattr("mailfilter.digest.run", boom)
    assert cli.main(["quarantine-report"]) == 0
    assert cli.main(["monthly-report"]) == 0
    assert capsys.readouterr().out.count("module_off") == 2
