"""Relay domains (outbound only), the sender lock and SMTP accounts."""
import base64
import hashlib
import json

import pytest

from mailfilter import gateway
from mailfilter.api import dns as dns_api
from mailfilter_dns.config import ServerConfig

from conftest import Client


@pytest.fixture(autouse=True)
def helper(monkeypatch):
    calls = []

    def fake(config_json):
        calls.append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", fake)
    server = ServerConfig("mailfilter.rhis.nl", ["203.0.113.1"], [], "_spf.mailfilter.rhis.nl")
    monkeypatch.setattr(dns_api, "get_server", lambda: server)
    return calls


RELAY = {"name": "Webshop-DeVries.nl", "kind": "relay", "sender_lock": True, "out_per_day": 5000,
         "destinations": [{"host": "ignored.example.nl"}]}


def test_relay_domain_has_no_destination(admin, helper):
    r = admin.post("/api/gateway/domains", json=RELAY)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["kind"] == "relay" and out["sender_lock"] is True and out["destinations"] == []
    dom = next(d for d in helper[-1]["domains"] if d["name"] == "webshop-devries.nl")
    assert dom["kind"] == "relay" and dom["destinations"] == [] and dom["sender_lock"] is True
    assert dom["outbound"]["per_day"] == 5000


def test_transport_domain_still_needs_a_server(admin, helper):
    r = admin.post("/api/gateway/domains", json={"name": "geenserver.nl", "kind": "transport"})
    assert r.status_code == 422
    assert r.json()["detail"]["problems"][0]["code"] == "destination_missing"


def test_convert_relay_to_transport(admin, helper):
    dom_id = admin.post("/api/gateway/domains", json=RELAY).json()["id"]
    r = admin.put(f"/api/gateway/domains/{dom_id}", json={**RELAY, "kind": "transport",
                                                          "destinations": [{"host": "mail.devries.nl"}]})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "transport" and r.json()["destinations"][0]["host"] == "mail.devries.nl"


def test_smtp_accounts(admin, helper):
    admin.post("/api/gateway/domains", json=RELAY)
    r = admin.post("/api/gateway/smtp-accounts", json={"username": "SMTP@webshop-devries.nl", "domain": "webshop-devries.nl",
                                                        "description": "Magento"})
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["username"] == "smtp@webshop-devries.nl" and len(acc["password"]) >= 20
    sent = helper[-1]["smtp_accounts"][0]
    assert sent["domain"] == "webshop-devries.nl"
    raw = base64.b64decode(sent["password_hash"].removeprefix("{SSHA512}"))
    digest, salt = raw[:64], raw[64:]
    assert hashlib.sha512(acc["password"].encode() + salt).digest() == digest  # Dovecot's SSHA512
    assert "password" not in admin.get("/api/gateway/smtp-accounts").json()[0]
    assert admin.post("/api/gateway/smtp-accounts", json={"username": "smtp@webshop-devries.nl",
                                                           "domain": "webshop-devries.nl"}).status_code == 409
    r = admin.post(f"/api/gateway/smtp-accounts/{acc['id']}/password")
    assert r.status_code == 200 and r.json()["password"] != acc["password"]
    r = admin.post("/api/gateway/smtp-accounts", json={"username": "bad name", "domain": "webshop-devries.nl"})
    assert r.status_code == 422 and r.json()["detail"]["problems"][0]["code"] == "account_invalid"
    domains = {d["name"]: d for d in admin.get("/api/gateway/domains").json()}
    assert domains["webshop-devries.nl"]["smtp_accounts"] == 1
    assert admin.delete(f"/api/gateway/smtp-accounts/{acc['id']}").status_code == 204
    assert helper[-1]["smtp_accounts"] == []


def test_customer_accounts_scope(users, admin, helper):
    for name in ("rhis.nl", "klant.nl"):
        assert admin.post("/api/gateway/domains", json={"name": name, "kind": "relay"}).status_code == 201
    assert admin.post("/api/gateway/smtp-accounts", json={"username": "admin-only", "domain": "rhis.nl"}).status_code == 201
    cust = Client().login(users["customer"])
    r = cust.post("/api/gateway/smtp-accounts", json={"username": "web@klant.nl", "domain": "klant.nl"})
    assert r.status_code == 201, r.text
    assert cust.post("/api/gateway/smtp-accounts", json={"username": "x", "domain": "rhis.nl"}).status_code == 403
    assert [a["username"] for a in cust.get("/api/gateway/smtp-accounts").json()] == ["web@klant.nl"]
    other = next(a for a in admin.get("/api/gateway/smtp-accounts").json() if a["username"] == "admin-only")
    assert cust.delete(f"/api/gateway/smtp-accounts/{other['id']}").status_code == 404
    assert cust.post(f"/api/gateway/smtp-accounts/{other['id']}/password").status_code == 404


def test_records(users, admin, helper, monkeypatch):
    from mailfilter_dns import dkim
    monkeypatch.setattr(dkim, "local_key", lambda d, *a, **k: dkim.DkimKey(d, "mf2026", "MIIBpub"))
    dom_id = admin.post("/api/gateway/domains", json=RELAY).json()["id"]
    r = admin.get(f"/api/gateway/domains/{dom_id}/records").json()
    assert r["dkim"]["name"] == "mf2026._domainkey.webshop-devries.nl"
    assert r["dkim"]["value"] == "v=DKIM1; k=rsa; p=MIIBpub"
    assert r["spf_include"] == "include:_spf.mailfilter.rhis.nl"
    assert "rua=mailto:" in r["dmarc_suggested"]
    cust = Client().login(users["customer"])
    assert cust.get(f"/api/gateway/domains/{dom_id}/records").status_code == 404
