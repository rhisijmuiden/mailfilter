import base64
import time

from mailfilter import twofactor
from mailfilter.db import session
from mailfilter.models import AuditLog, User

from conftest import PASSWORD, Client


def code(secret: str, offset: int = 0) -> str:
    return twofactor.totp(secret, time.time() + offset * twofactor.STEP)


def enable(c: Client) -> tuple[str, list[str]]:
    r = c.post("/api/auth/2fa/setup")
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["uri"].startswith("otpauth://totp/Mailfilter:")
    assert r.json()["qr_svg"].startswith("<svg")
    assert c.post("/api/auth/2fa/enable", json={"code": "000000" if code(secret) != "000000" else "111111"}).status_code == 400
    r = c.post("/api/auth/2fa/enable", json={"code": code(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["codes"]


def test_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert twofactor.totp(secret, 59) == "287082"
    assert twofactor.totp(secret, 1111111109) == "081804"


def test_login_with_totp_and_no_replay(admin, users):
    secret, codes = enable(admin)
    assert len(codes) == 10
    assert admin.get("/api/auth/me").json()["two_factor_enabled"] is True

    c = Client()
    r = c.post("/api/auth/login", json={"email": users["admin"], "password": PASSWORD})
    assert r.status_code == 200 and r.json()["two_factor_required"] is True
    assert c.get("/api/auth/me").status_code == 401  # no session yet
    challenge = r.json()["challenge"]
    assert c.post("/api/auth/login/2fa", json={"challenge": challenge, "code": "12345"}).status_code == 401
    # The code used to enable is already spent.
    assert c.post("/api/auth/login/2fa", json={"challenge": challenge, "code": code(secret)}).status_code == 401
    assert c.post("/api/auth/login/2fa", json={"challenge": challenge + "x", "code": code(secret, 1)}).status_code == 401
    r = c.post("/api/auth/login/2fa", json={"challenge": challenge, "code": code(secret, 1)})
    assert r.status_code == 200, r.text
    assert c.get("/api/auth/me").json()["email"] == users["admin"]


def test_recovery_code_works_once(customer, users):
    _, codes = enable(customer)
    for expected in (200, 401):
        c = Client()
        ch = c.post("/api/auth/login", json={"email": users["customer"], "password": PASSWORD}).json()["challenge"]
        assert c.post("/api/auth/login/2fa", json={"challenge": ch, "code": codes[0].upper()}).status_code == expected
    assert customer.get("/api/auth/2fa").json()["recovery_codes_left"] == 9
    with session() as db:
        methods = [a.detail.get("method") for a in db.query(AuditLog).filter_by(action="login")]
    assert "recovery_code" in methods


def test_disable_and_admin_reset(admin, customer, users):
    secret, _ = enable(customer)
    assert customer.post("/api/auth/2fa/disable", json={"password": "wrong", "code": code(secret, 1)}).status_code == 400
    assert customer.post("/api/auth/2fa/disable", json={"password": PASSWORD, "code": code(secret, 1)}).status_code == 204
    assert customer.get("/api/auth/me").json()["two_factor_enabled"] is False

    enable(customer)
    with session() as db:
        uid = db.query(User).filter_by(email=users["customer"]).one().id
    listed = {u["email"]: u for u in admin.get("/api/users").json()}
    assert listed[users["customer"]]["two_factor_enabled"] is True
    assert customer.post(f"/api/users/{uid}/2fa/reset").status_code == 403
    assert admin.post(f"/api/users/{uid}/2fa/reset").json()["two_factor_enabled"] is False
    assert customer.get("/api/auth/me").status_code == 401  # logged out
    Client().login(users["customer"])  # password alone works again


def test_policy_forces_setup(admin, customer, users):
    assert admin.put("/api/system/security", json={"require_2fa": "all"}).json()["detail"] == "enable_two_factor_first"
    enable(admin)
    assert admin.put("/api/system/security", json={"require_2fa": "all"}).status_code == 200
    assert admin.get("/api/system/security").json() == {"require_2fa": "all"}

    me = customer.get("/api/auth/me").json()
    assert me["two_factor_setup_required"] is True
    assert customer.get("/api/trace").json()["detail"] == "two_factor_setup_required"
    secret, _ = enable(customer)
    assert customer.get("/api/auth/me").json()["two_factor_setup_required"] is False
    assert customer.get("/api/audit").status_code == 200
    assert customer.post("/api/auth/2fa/disable", json={"password": PASSWORD, "code": code(secret, 1)}).json()["detail"] == "two_factor_required_by_policy"

    assert admin.put("/api/system/security", json={"require_2fa": "admins"}).status_code == 200
    assert customer.put("/api/system/security", json={"require_2fa": "off"}).status_code == 403


def test_audit_scope_and_filters(admin, customer, users):
    admin.post("/api/users", json={"email": "nieuw@klant.nl", "domains": ["klant.nl"]})
    admin.post("/api/users", json={"email": "ander@rhis.nl"})
    customer.patch("/api/auth/me", json={"language": "en"})

    rows = admin.get("/api/audit").json()
    actions = [i["action"] for i in rows["items"]]
    assert actions.count("user_created") == 2 and rows["total"] >= 4

    created = [i for i in rows["items"] if i["action"] == "user_created"]
    assert {i["domain"] for i in created} == {"klant.nl", "rhis.nl"}
    assert all(i["user_email"] == users["admin"] and i["user_name"] == "Reinder" for i in created)

    with session() as db:
        aid = db.query(User).filter_by(email=users["admin"]).one().id
        db.add(AuditLog(user_id=aid, user_email=users["admin"], action="domain_updated", target="klant.nl", domain="klant.nl", detail={}, ip="192.0.2.7"))
        db.commit()
    assert admin.get("/api/audit", params={"action": "domain_updated"}).json()["items"][0]["ip"] == "192.0.2.7"
    seen = customer.get("/api/audit", params={"action": "domain_updated"}).json()["items"]
    assert len(seen) == 1 and seen[0]["ip"] is None

    # Customer: own logins + entries for klant.nl, without the admin's IP.
    mine = customer.get("/api/audit").json()["items"]
    targets = {(i["action"], i["target"]) for i in mine}
    assert ("user_created", "nieuw@klant.nl") in targets
    assert ("user_created", "ander@rhis.nl") not in targets
    assert ("login", users["admin"]) not in targets
    assert all(i["ip"] is None for i in mine if i["user_email"] == users["admin"])
    assert customer.get("/api/audit", params={"domain": "rhis.nl"}).json()["total"] == 0

    assert admin.get("/api/audit", params={"action": "user", "q": "ander"}).json()["total"] == 1
    assert admin.get("/api/audit", params={"domain": "klant.nl"}).json()["total"] >= 1
    f = admin.get("/api/audit/filters").json()
    assert "user_created" in f["actions"] and "klant.nl" in f["domains"]
    assert users["customer"] not in [u["email"] for u in customer.get("/api/audit/filters").json()["users"] if u["email"] == users["admin"]]
    csv = admin.get("/api/audit/export.csv", params={"action": "user_created"})
    assert csv.headers["content-type"].startswith("text/csv") and csv.text.count("\n") == 3

    # Entries stay readable after the user is deleted.
    uid = next(u["id"] for u in admin.get("/api/users").json() if u["email"] == "ander@rhis.nl")
    admin.delete(f"/api/users/{uid}")
    assert admin.get("/api/audit", params={"action": "user_deleted"}).json()["items"][0]["target"] == "ander@rhis.nl"
