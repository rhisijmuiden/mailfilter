import json

import pytest

from mailfilter import gateway
from mailfilter.db import session
from mailfilter.models import Domain, DomainRoute
from mailfilter.settings_store import get_setting

from conftest import Client


@pytest.fixture(autouse=True)
def helper(monkeypatch):
    """Stands in for `sudo mailfilter-config apply --stdin --json`."""
    calls = []
    state = {"result": {"ok": True, "changed": ["/etc/mailfilter/postfix/transport"], "warnings": []}}

    def fake(config_json):
        calls.append(json.loads(config_json))
        return state["result"]

    monkeypatch.setattr(gateway, "run_helper", fake)
    state["calls"] = calls
    return state


DOMAIN = {"name": "Voorbeeld.nl", "destinations": [{"host": "mail.voorbeeld.nl"}, {"host": "10.0.0.5", "port": 2525}],
          "concurrency": 3, "out_per_hour": 500}


def test_create_domain_applies_config(admin, helper):
    r = admin.post("/api/gateway/domains", json=DOMAIN)
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "voorbeeld.nl"
    cfg = helper["calls"][-1]
    dom = next(d for d in cfg["domains"] if d["name"] == "voorbeeld.nl")
    assert [d["host"] for d in dom["destinations"]] == ["mail.voorbeeld.nl", "10.0.0.5"]
    assert dom["queue"]["concurrency"] == 3 and dom["outbound"]["per_hour"] == 500
    listed = {d["name"]: d for d in admin.get("/api/gateway/domains").json()}
    assert listed["voorbeeld.nl"]["configured"] is True
    assert listed["rhis.nl"]["configured"] is False  # exists for users, not routed yet


def test_invalid_config_is_refused_with_both_languages(admin, helper):
    r = admin.post("/api/gateway/domains", json={"name": "loop.nl", "destinations": [{"host": "loop.nl", "mx": True}]})
    assert r.status_code == 422
    p = r.json()["detail"]["problems"][0]
    assert p["code"] == "loop_mx" and p["en"] and p["nl"]
    assert helper["calls"] == []
    with session() as db:
        assert db.query(Domain).filter_by(name="loop.nl").first() is None


def test_server_refusal_rolls_back(admin, helper):
    helper["result"] = {"ok": False, "step": "postfix check", "output": "fatal: broken"}
    r = admin.post("/api/gateway/domains", json=DOMAIN)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "apply_failed"
    with session() as db:
        assert db.query(Domain).filter_by(name="voorbeeld.nl").first() is None
        assert get_setting(db, gateway.LAST_APPLY_KEY)["ok"] is False


def test_update_and_delete_domain(admin, helper):
    dom_id = admin.post("/api/gateway/domains", json=DOMAIN).json()["id"]
    body = {**DOMAIN, "destinations": [{"host": "mx2.voorbeeld.nl"}], "enabled": False}
    r = admin.put(f"/api/gateway/domains/{dom_id}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["destinations"][0]["host"] == "mx2.voorbeeld.nl"
    assert admin.delete(f"/api/gateway/domains/{dom_id}").status_code == 204
    assert all(d["name"] != "voorbeeld.nl" for d in helper["calls"][-1]["domains"])


def test_relay_networks_and_open_relay(admin, helper):
    r = admin.post("/api/gateway/relay-networks", json={"cidr": "203.0.113.25/32", "description": "Web 1"})
    assert r.status_code == 201, r.text
    assert r.json()["cidr"] == "203.0.113.25"
    assert admin.post("/api/gateway/relay-networks", json={"cidr": "203.0.113.25"}).status_code == 409
    r = admin.post("/api/gateway/relay-networks", json={"cidr": "0.0.0.0/0"})
    assert r.status_code == 422 and r.json()["detail"]["problems"][0]["code"] == "open_relay"
    r = admin.post("/api/gateway/relay-networks", json={"cidr": "2a02:58::/24", "allow_broad": True})
    assert r.status_code == 201, r.text


def test_customer_scope(users, admin, helper):
    admin.post("/api/gateway/relay-networks", json={"cidr": "198.51.100.1", "domain": "rhis.nl"})
    cust = Client().login(users["customer"])
    assert [d["name"] for d in cust.get("/api/gateway/domains").json()] == ["klant.nl"]
    assert cust.get("/api/gateway/relay-networks").json() == []
    # a customer may add relay IPs for their own domain only, and never broad networks
    r = cust.post("/api/gateway/relay-networks", json={"cidr": "192.0.2.10", "domain": "klant.nl"})
    assert r.status_code == 201, r.text
    assert cust.post("/api/gateway/relay-networks", json={"cidr": "192.0.2.11", "domain": "rhis.nl"}).status_code == 403
    assert cust.post("/api/gateway/relay-networks", json={"cidr": "192.0.2.12"}).status_code == 400
    r = cust.post("/api/gateway/relay-networks", json={"cidr": "10.0.0.0/8", "domain": "klant.nl", "allow_broad": True})
    assert r.status_code == 422
    others = admin.get("/api/gateway/relay-networks").json()
    rhis_net = next(n for n in others if n["domain"] == "rhis.nl")
    assert cust.delete(f"/api/gateway/relay-networks/{rhis_net['id']}").status_code == 404
    # admin-only screens
    for path in ("/api/gateway/relay-ports", "/api/gateway/throttling", "/api/gateway/address-routes"):
        assert cust.get(path).status_code == 403
    assert cust.post("/api/gateway/domains", json=DOMAIN).status_code == 403


def test_ports_and_throttling(admin, helper):
    assert admin.post("/api/gateway/relay-ports", json={"port": 587}).status_code == 422
    r = admin.post("/api/gateway/relay-ports", json={"port": 2525, "description": "Old webshop"})
    assert r.status_code == 201, r.text
    t = admin.get("/api/gateway/throttling").json()
    assert t["defaults"]["relay_ip_per_minute"] == 30
    t["defaults"]["relay_ip_per_minute"] = 60
    t["policies"] = [{"domain": "Gmail.com", "concurrency": 5, "rate_delay": 1}]
    r = admin.put("/api/gateway/throttling", json=t)
    assert r.status_code == 200, r.text
    assert r.json()["policies"][0]["domain"] == "gmail.com"
    cfg = helper["calls"][-1]
    assert cfg["defaults"]["relay_ip_per_minute"] == 60
    assert cfg["relay_ports"][0]["port"] == 2525
    assert cfg["destination_policies"][0]["queue"]["rate_delay"] == 1
    r = admin.put("/api/gateway/throttling", json={**r.json(), "policies": []})
    assert r.json()["policies"] == []


def test_routes_and_tls(admin, helper):
    r = admin.post("/api/gateway/address-routes", json={"address": "Info@Elders.nl", "host": "10.0.0.9", "port": 24,
                                                         "protocol": "lmtp"})
    assert r.status_code == 201, r.text
    assert r.json()["address"] == "info@elders.nl"
    r = admin.post("/api/gateway/tls-policies", json={"destination": ".bank.nl", "level": "secure"})
    assert r.status_code == 201, r.text
    cfg = helper["calls"][-1]
    assert cfg["address_routes"][0]["destination"]["protocol"] == "lmtp"
    assert cfg["tls_policies"][0]["level"] == "secure"


def test_import_from_installer_state(tmp_path, users):
    state = tmp_path / "gateway.json"
    state.write_text(json.dumps({
        "domains": [{"name": "klant.nl", "destinations": [{"host": "mail.klant.nl", "port": 25}]}],
        "relay_networks": [{"cidr": "203.0.113.7", "description": "From installer"}],
        "relay_ports": [{"port": 2525}],
    }))
    with session() as db:
        gateway.ensure_imported(db, state)
        gateway.ensure_imported(db, state)  # only once
        route = db.query(DomainRoute).join(Domain).filter(Domain.name == "klant.nl").one()
        assert route.destinations[0].host == "mail.klant.nl"
        cfg = gateway.build_config(db)
        assert [n.cidr for n in cfg.relay_networks] == ["203.0.113.7"]
        assert [p.port for p in cfg.relay_ports] == [2525]


def test_customer_relay_networks_are_locked_to_their_domains(users, admin, helper):
    """Relay IPs of a customer's domain may only send as that customer's domains;
    admin networks without a domain stay unrestricted."""
    admin.post("/api/gateway/relay-networks", json={"cidr": "198.51.100.1"})
    cust = Client().login(users["customer"])
    assert cust.post("/api/gateway/relay-networks", json={"cidr": "192.0.2.10", "domain": "klant.nl"}).status_code == 201
    nets = {n["cidr"]: n for n in helper["calls"][-1]["relay_networks"]}
    assert nets["192.0.2.10"]["senders"] == ["klant.nl"]
    assert nets["198.51.100.1"]["senders"] == []

    # The customer gets a second domain: the lock follows at once.
    with session() as db:
        db.add(Domain(name="klant2.nl"))
        db.commit()
    cid = next(u["id"] for u in admin.get("/api/users").json() if u["email"] == users["customer"])
    before = len(helper["calls"])
    r = admin.put(f"/api/users/{cid}", json={"email": "info@klant.nl", "name": "Klant", "role": "customer",
                                             "language": "nl", "is_active": True, "domains": ["klant.nl", "klant2.nl"]})
    assert r.status_code == 200, r.text
    assert len(helper["calls"]) == before + 1
    nets = {n["cidr"]: n for n in helper["calls"][-1]["relay_networks"]}
    assert nets["192.0.2.10"]["senders"] == ["klant.nl", "klant2.nl"]
