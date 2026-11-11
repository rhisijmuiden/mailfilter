from fastapi.testclient import TestClient

from mailfilter.main import app


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_login_logout(client, users):
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "ADMIN@rhis.nl", "password": "correct horse battery"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["email"] == "admin@rhis.nl"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_throttle(client, users):
    for _ in range(8):
        assert client.post("/api/auth/login", json={"email": "admin@rhis.nl", "password": "nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "admin@rhis.nl", "password": "correct horse battery"})
    assert r.status_code == 429


def test_csrf_header_required(users):
    bare = TestClient(app)
    r = bare.post("/api/auth/login", json={"email": "admin@rhis.nl", "password": "correct horse battery"})
    assert r.status_code == 403


def test_language_and_password(customer):
    assert customer.get("/api/auth/me").json()["language"] == "nl"
    assert customer.patch("/api/auth/me", json={"language": "en"}).json()["language"] == "en"
    assert customer.post("/api/auth/me/password", json={"current": "wrong", "new": "a-new-password"}).status_code == 400
    assert customer.post("/api/auth/me/password", json={"current": "correct horse battery", "new": "a-new-password"}).status_code == 204
    customer.post("/api/auth/logout")
    customer.login("info@klant.nl", "a-new-password")


def test_system_info(admin):
    info = admin.get("/api/system/info").json()
    assert info["version"] and info["branch"]
