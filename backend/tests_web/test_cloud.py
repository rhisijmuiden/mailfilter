"""Cloud mail: Microsoft 365 / Google Workspace tenants behind the Mailfilter."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mailfilter import gateway, mailer
from mailfilter.api import cloud as cloud_api
from mailfilter.api import dns as dns_api
from mailfilter.db import session
from mailfilter.models import CloudTenant, TraceMessage, TraceRecipient, User
from mailfilter_dns.config import ServerConfig
from mailfilter_dns.resolver import StaticResolver

TENANT = "8c1f2b6e-0d3a-4c55-9e1b-6f0a2d7be42a"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    calls = []

    def fake(config_json):
        calls.append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", fake)
    server = ServerConfig("mailfilter.rhis.nl", ["203.0.113.1"], ["2001:db8::25"], "_spf.mailfilter.rhis.nl")
    monkeypatch.setattr(dns_api, "get_server", lambda: server)
    resolver = StaticResolver({
        ("klant.nl", "MX"): [(0, "klant-nl.mail.protection.outlook.com")],
        ("klant.nl", "TXT"): ["v=spf1 include:spf.protection.outlook.com -all"],
        ("bakkerij.nl", "MX"): [(1, "aspmx.l.google.com"), (5, "alt1.aspmx.l.google.com")],
    })
    monkeypatch.setattr(dns_api, "get_resolver", lambda: resolver)
    monkeypatch.setattr(cloud_api, "fetch_tenant_id", lambda d: TENANT if d == "klant.nl" else None)
    return {"calls": calls, "resolver": resolver}


def link(admin, domain="klant.nl", provider="m365", tenant_id=TENANT, **extra):
    r = admin.post("/api/cloud", json={"domain": domain, "provider": provider, "tenant_id": tenant_id, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_detect(admin):
    r = admin.get("/api/cloud/detect/klant.nl").json()
    assert r["provider"] == "m365" and r["tenant_id"] == TENANT
    assert r["destinations"] == ["klant-nl.mail.protection.outlook.com"]
    assert r["exists"] is True and r["configured"] is False and r["customer_ids"]
    g = admin.get("/api/cloud/detect/bakkerij.nl").json()
    assert g["provider"] == "google" and g["destinations"][0] == "aspmx.l.google.com" and g["exists"] is False


def test_link_sets_up_mailfilter(admin, env):
    out = link(admin)
    assert out["destinations"] == ["klant-nl.mail.protection.outlook.com"]
    assert [s["state"] for s in out["steps"][:2]] == ["done", "done"]
    cfg = env["calls"][-1]
    dom = next(d for d in cfg["domains"] if d["name"] == "klant.nl")
    assert dom["kind"] == "transport" and dom["tls"] == "encrypt"
    assert cfg["cloud_tenants"] == [{"provider": "m365", "domain": "klant.nl", "tenant_id": TENANT, "enabled": True}]
    assert admin.post("/api/cloud", json={"domain": "klant.nl", "provider": "m365", "tenant_id": TENANT}).status_code == 409


def test_google_new_domain_and_customer(admin, env):
    customer_id = admin.get("/api/cloud/detect/klant.nl").json()["customer_ids"][0]
    out = link(admin, "bakkerij.nl", "google", "", customer_id=customer_id)
    assert out["destinations"][0] == "aspmx.l.google.com" and len(out["destinations"]) == 5
    assert [s["id"] for s in out["steps"]][2:5] == ["dkim", "inbound_gateway", "outbound_gateway"]
    with session() as db:
        cust = db.query(User).filter_by(email="info@klant.nl").one()
        assert "bakkerij.nl" in cust.domain_names


def test_bad_tenant_id_refused(admin):
    r = admin.post("/api/cloud", json={"domain": "klant.nl", "provider": "m365", "tenant_id": "nope"})
    assert r.status_code == 422
    assert r.json()["detail"]["problems"][0]["code"] == "tenant_id_invalid"


def test_steps_and_evidence(admin, customer):
    link(admin)
    d = customer.get("/api/cloud/klant.nl").json()
    states = {s["id"]: s["state"] for s in d["steps"]}
    assert states["inbound_connector"] == "todo" and states["dns"] == "todo"
    assert d["dns"]["mx"] == "missing" and d["dns"]["spf"] == "missing"
    assert d["dns"]["spf_fix"] == "v=spf1 include:spf.protection.outlook.com include:_spf.mailfilter.rhis.nl -all"

    # The customer ticks off the outbound connector: waiting until mail is seen.
    r = customer.post("/api/cloud/klant.nl/steps/outbound_connector", json={"done": True}).json()
    assert {s["id"]: s["state"] for s in r["steps"]}["outbound_connector"] == "waiting"
    assert customer.post("/api/cloud/klant.nl/steps/dns", json={"done": True}).status_code == 400

    with session() as db:
        db.add(TraceMessage(queue_id="Q1", first_seen=NOW, last_seen=NOW, status="sent", sender="jan@klant.nl",
                            direction="outbound", symbols={"MF_CLOUD_TENANT_OK": 0.0}))
        db.add(TraceMessage(queue_id="Q2", first_seen=NOW, last_seen=NOW, status="sent", sender="x@other.nl",
                            direction="inbound", symbols={}))
        db.add(TraceRecipient(queue_id="Q2", address="jan@klant.nl", status="delivered",
                              relay_host="klant-nl.mail.protection.outlook.com"))
        db.add(TraceMessage(queue_id="Q3", first_seen=NOW, last_seen=NOW, status="rejected", sender="ceo@klant.nl",
                            direction="outbound", symbols={"MF_CLOUD_TENANT_FORGED": 0.0}))
        db.commit()
    d = admin.get("/api/cloud/klant.nl").json()
    states = {s["id"]: s["state"] for s in d["steps"]}
    assert states["outbound_connector"] == "done" and states["inbound_connector"] == "done" and states["test"] == "done"
    assert d["evidence"]["forged_7d"] == 1
    lst = admin.get("/api/cloud").json()
    assert lst[0]["domain"] == "klant.nl" and lst[0]["summary"]["done"] == d["summary"]["done"]
    assert lst[0]["customers"] == ["info@klant.nl"]


def test_dns_and_lockdown_wait(admin, env):
    link(admin)
    env["resolver"].records[("klant.nl", "MX")] = [(10, "mailfilter.rhis.nl")]
    env["resolver"].records[("klant.nl", "TXT")] = ["v=spf1 include:spf.protection.outlook.com include:_spf.mailfilter.rhis.nl -all"]
    d = admin.get("/api/cloud/klant.nl").json()
    assert {s["id"]: s["state"] for s in d["steps"]}["dns"] == "done"
    assert d["evidence"]["mx_since"] and d["evidence"]["lockdown_ready"] is False
    with session() as db:
        t = db.query(CloudTenant).one()
        t.steps = {**t.steps, "dns": {"mx_since": (NOW - timedelta(hours=49)).isoformat()}}
        db.commit()
    assert admin.get("/api/cloud/klant.nl").json()["evidence"]["lockdown_ready"] is True


def test_customer_scope(admin, customer):
    link(admin, "bakkerij.nl", "google", "")
    assert customer.get("/api/cloud").json() == []
    assert customer.get("/api/cloud/bakkerij.nl").status_code == 404
    assert customer.post("/api/cloud", json={"domain": "klant.nl", "provider": "m365", "tenant_id": TENANT}).status_code == 403
    assert customer.get("/api/cloud/detect/klant.nl").status_code == 403


def test_script(admin):
    link(admin)
    s = admin.get("/api/cloud/klant.nl/script").text
    assert 'New-InboundConnector -Name "Mailfilter inbound"' in s and '"203.0.113.1"' in s
    assert "2001:db8::25" not in s  # Microsoft takes IPv4 only here
    assert "-SmartHosts mailfilter.rhis.nl" in s and "-EFSkipLastIP $true" in s
    assert "-RestrictDomainsToIPAddresses $true" in admin.get("/api/cloud/klant.nl/script?part=lockdown").text
    link(admin, "bakkerij.nl", "google", "")
    assert admin.get("/api/cloud/bakkerij.nl/script").status_code == 400


def test_test_mail_only_to_the_domain(admin, monkeypatch):
    link(admin)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda db, to, subject, text: sent.append(to) or "<id@x>")
    assert admin.post("/api/cloud/klant.nl/test", json={"to": "someone@elsewhere.nl"}).status_code == 400
    assert admin.post("/api/cloud/klant.nl/test", json={"to": "jan@klant.nl"}).status_code == 200
    assert sent == [["jan@klant.nl"]]


def test_unlink_and_disable(admin, env):
    link(admin)
    r = admin.put("/api/cloud/klant.nl", json={"enabled": False})
    assert r.status_code == 200 and env["calls"][-1]["cloud_tenants"][0]["enabled"] is False
    assert admin.delete("/api/cloud/klant.nl").status_code == 204
    assert env["calls"][-1]["cloud_tenants"] == []
    assert any(d["name"] == "klant.nl" for d in env["calls"][-1]["domains"])  # the domain stays
