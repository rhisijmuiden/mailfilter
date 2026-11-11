def test_customer_cannot_manage_users(customer):
    assert customer.get("/api/users").status_code == 403


def test_admin_creates_customer_with_domains(admin, client):
    r = admin.post("/api/users", json={"email": "piet@klant.nl", "role": "customer", "domains": ["klant.nl"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["domains"] == ["klant.nl"] and len(body["password"]) >= 12
    client.login("piet@klant.nl", body["password"])
    assert client.get("/api/auth/me").json()["domains"] == ["klant.nl"]


def test_unknown_domain_rejected(admin):
    r = admin.post("/api/users", json={"email": "x@y.nl", "domains": ["nope.nl"]})
    assert r.status_code == 400


def test_admin_cannot_demote_or_delete_self(admin):
    me = admin.get("/api/auth/me").json()
    r = admin.put(f"/api/users/{me['id']}", json={"email": me["email"], "role": "customer"})
    assert r.status_code == 400
    assert admin.delete(f"/api/users/{me['id']}").status_code == 400


def test_disable_user_logs_them_out(admin, customer):
    users = {u["email"]: u for u in admin.get("/api/users").json()}
    c = users["info@klant.nl"]
    r = admin.put(f"/api/users/{c['id']}", json={**c, "is_active": False})
    assert r.status_code == 200
    assert customer.get("/api/auth/me").status_code == 401


def test_settings_store_secret():
    from mailfilter.db import session
    from mailfilter.settings_store import get_secret, get_setting, set_secret, set_setting

    with session() as db:
        set_setting(db, "smtp", {"host": "smtp.example.nl"})
        set_secret(db, "cloudflare_token", "abc123")
        db.commit()
        assert get_setting(db, "smtp")["host"] == "smtp.example.nl"
        assert get_secret(db, "cloudflare_token") == "abc123"
        assert "abc123" not in str(get_setting(db, "cloudflare_token"))
