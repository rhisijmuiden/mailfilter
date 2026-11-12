import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mailfilter import apikeys, gateway
from mailfilter.db import session
from mailfilter.main import app
from mailfilter.models import ApiKey, AuditLog


@pytest.fixture(autouse=True)
def helper(monkeypatch):
    monkeypatch.setattr(gateway, "run_helper", lambda cfg: {"ok": True, "changed": [], "warnings": []})
    apikeys.rate_limiter._hits.clear()


def api(token: str | None) -> TestClient:
    """A plain client, like a hosting panel: no session cookie, no X-Requested-With."""
    return TestClient(app, headers={"Authorization": f"Bearer {token}"} if token else {})


def make_key(client, **body) -> dict:
    body = {"name": "panel", "scopes": {"domains": "read", "relay": "write"}, **body}
    r = client.post("/api/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_admin_key_works_on_v1_only(admin):
    k = make_key(admin)
    assert k["key"].startswith("mfk_") and k["hint"] == k["key"][:12]
    c = api(k["key"])
    me = c.get("/api/v1/me").json()
    assert me["owner"] == "admin@rhis.nl" and me["domains"] is None and me["key_name"] == "panel"
    names = {d["name"] for d in c.get("/api/v1/gateway/domains").json()}
    assert names == {"rhis.nl", "klant.nl"}
    # The key is no login for the web app's API, and a cookie is no key.
    assert c.get("/api/gateway/domains").status_code == 401
    assert admin.get("/api/v1/gateway/domains").json()["detail"] == "api_key_required"
    # Only the hash is stored, and the list never shows the key.
    listed = admin.get("/api/api-keys").json()
    assert "key" not in listed[0] and listed[0]["last_used_at"] is not None
    with session() as db:
        assert db.query(ApiKey).one().token_hash != k["key"]


def test_write_needs_write_scope_and_no_csrf_header(admin):
    c = api(make_key(admin, scopes={"domains": "read", "relay": "write"})["key"])
    r = c.post("/api/v1/gateway/relay-networks", json={"cidr": "203.0.113.10", "domain": "klant.nl", "description": "web"})
    assert r.status_code == 201, r.text
    r = c.post("/api/v1/gateway/domains", json={"name": "nieuw.nl", "destinations": [{"host": "mail.nieuw.nl"}]})
    assert r.status_code == 403 and r.json()["detail"] == "scope_missing:domains:write"
    assert c.get("/api/v1/quarantine").json()["detail"] == "scope_missing:quarantine:read"
    with session() as db:
        row = db.query(AuditLog).filter_by(action="relay_network_added").one()
        assert row.detail["api_key"] == "panel" and row.user_email == "admin@rhis.nl"


def test_other_areas_are_not_in_the_api(admin):
    c = api(make_key(admin, scopes={a: "write" for a in apikeys.AREA_KEYS})["key"])
    for path in ("/api/v1/gateway/throttling", "/api/v1/gateway/relay-ports", "/api/v1/system/security",
                 "/api/v1/users/1/2fa/reset"):
        r = c.get(path) if "reset" not in path else c.post(path)
        assert r.status_code == 403 and r.json()["detail"] == "not_available_in_api", path
    assert c.get("/api/v1/api-keys").status_code == 404


def test_customer_key_sees_own_domains_only(customer):
    with pytest.raises(AssertionError):
        make_key(customer, scopes={"users": "read"})
    assert customer.post("/api/api-keys", json={"name": "x", "scopes": {"users": "read"}}).json()["detail"] == "scope_admin_only:users"
    c = api(make_key(customer)["key"])
    assert [d["name"] for d in c.get("/api/v1/gateway/domains").json()] == ["klant.nl"]
    assert c.post("/api/v1/gateway/relay-networks", json={"cidr": "198.51.100.7", "domain": "klant.nl"}).status_code == 201
    r = c.post("/api/v1/gateway/relay-networks", json={"cidr": "198.51.100.8", "domain": "rhis.nl"})
    assert r.status_code == 403 and r.json()["detail"] == "not_your_domain"


def test_customer_cannot_see_or_make_keys_of_others(admin, customer):
    mine = make_key(admin)
    assert customer.get("/api/api-keys").json() == []
    assert customer.delete(f"/api/api-keys/{mine['id']}").status_code == 404
    assert customer.post("/api/api-keys", json={"name": "x", "scopes": {"domains": "read"}, "owner": "admin@rhis.nl"}).status_code == 403
    # An admin can make a key for a customer; it then acts as that customer.
    k = make_key(admin, owner="info@klant.nl")
    assert api(k["key"]).get("/api/v1/me").json()["domains"] == ["klant.nl"]
    assert len(customer.get("/api/api-keys").json()) == 1


def test_ip_allowlist_expiry_and_revoke(admin):
    k = make_key(admin, allowed_ips=["192.0.2.0/24", " 2001:db8::1 "])
    assert k["allowed_ips"] == ["192.0.2.0/24", "2001:db8::1"]
    assert api(k["key"]).get("/api/v1/me").json()["detail"] == "ip_not_allowed"

    k2 = make_key(admin, name="old")
    with session() as db:
        db.get(ApiKey, k2["id"]).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert api(k2["key"]).get("/api/v1/me").json()["detail"] == "api_key_expired"

    k3 = make_key(admin, name="gone")
    assert admin.delete(f"/api/api-keys/{k3['id']}").status_code == 204
    r = api(k3["key"]).get("/api/v1/me")
    assert r.status_code == 401 and r.json()["detail"] == "invalid_api_key"
    assert api("mfk_nonsense").get("/api/v1/me").status_code == 401
    assert api(None).get("/api/v1/me").json()["detail"] == "api_key_required"
    with session() as db:
        actions = {a for a, in db.query(AuditLog.action)}
    assert {"api_key_created", "api_key_revoked"} <= actions


def test_bad_input(admin):
    assert admin.post("/api/api-keys", json={"name": "x", "scopes": {}}).json()["detail"] == "scopes_required"
    assert admin.post("/api/api-keys", json={"name": "x", "scopes": {"domains": "read"}, "allowed_ips": ["nope"]}).json()["detail"] == "invalid_ip:nope"
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert admin.post("/api/api-keys", json={"name": "x", "scopes": {"domains": "read"}, "expires_at": past}).json()["detail"] == "expiry_in_past"
    # Write on a read-only area becomes read.
    assert make_key(admin, scopes={"trace": "write"})["scopes"] == {"trace": "read"}


def test_update_key(admin):
    k = make_key(admin)
    r = admin.put(f"/api/api-keys/{k['id']}", json={"name": "renamed", "scopes": {"trace": "read"}})
    assert r.status_code == 200 and r.json()["scopes"] == {"trace": "read"}
    c = api(k["key"])
    assert c.get("/api/v1/gateway/domains").status_code == 403
    assert c.get("/api/v1/trace").status_code == 200


def test_api_cannot_manage_admin_accounts(admin):
    c = api(make_key(admin, scopes={"users": "write"})["key"])
    r = c.post("/api/v1/users", json={"email": "evil@rhis.nl", "role": "admin"})
    assert r.status_code == 403 and r.json()["detail"] == "api_customers_only"
    r = c.post("/api/v1/users", json={"email": "nieuw@klant.nl", "domains": ["klant.nl"]})
    assert r.status_code == 201 and r.json()["password"]
    admin_id = next(u["id"] for u in c.get("/api/v1/users").json() if u["role"] == "admin")
    assert c.delete(f"/api/v1/users/{admin_id}").json()["detail"] == "api_customers_only"


def test_rate_limit(admin, monkeypatch):
    monkeypatch.setattr(apikeys.rate_limiter, "limit", 2)
    c = api(make_key(admin)["key"])
    assert [c.get("/api/v1/me").status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/api/v1/me").headers["retry-after"]


def test_openapi_lists_only_key_paths(client):
    spec = client.get("/api/v1/openapi.json").json()
    paths = spec["paths"]
    assert "/api/v1/gateway/relay-networks" in paths and "/api/v1/me" in paths
    assert not any("throttling" in p or "api-keys" in p or "backup" in p for p in paths)
    assert paths["/api/v1/gateway/relay-networks"]["post"]["x-scope"] == "relay:write"
    assert spec["components"]["securitySchemes"]["apiKey"]["scheme"] == "bearer"
    # The web app's own description does not list /api/v1.
    assert not any(p.startswith("/api/v1") for p in client.get("/api/openapi.json").json()["paths"])


def test_keys_travel_with_cluster_snapshot(admin):
    from mailfilter.cluster import snapshot

    k = make_key(admin, owner="info@klant.nl", scopes={"relay": "write"})
    with session() as db:
        snap = snapshot.export(db, with_dkim=False)
        assert snap["api_keys"][0]["owner"] == "info@klant.nl"
        snapshot._import_api_keys(db, [])
        db.commit()
        assert db.query(ApiKey).count() == 0
        snapshot._import_api_keys(db, json.loads(json.dumps(snap["api_keys"], default=str)))
        db.commit()
    assert api(k["key"]).get("/api/v1/me").json()["owner"] == "info@klant.nl"
