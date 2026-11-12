import json
import smtplib
import subprocess

import pytest

import mailfilter_config as mfc
from mailfilter import gateway, recipients
from mailfilter.cluster import snapshot
from mailfilter.db import session
from mailfilter.models import DomainRoute


@pytest.fixture(autouse=True)
def helper(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway, "run_helper", lambda cfg: calls.append(json.loads(cfg)) or {"ok": True, "changed": [], "warnings": []})
    return calls


def _domain(admin, name="klant.nl"):
    r = admin.post("/api/gateway/domains", json={"name": name, "destinations": [{"host": "mail.klant.nl"}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _sent(helper, name="klant.nl"):
    return next(d for d in helper[-1]["domains"] if d["name"] == name)


LDAP = {"servers": ["dc1.klant.nl"], "security": "starttls", "bind_dn": "CN=svc,DC=klant,DC=nl",
        "bind_password": "geheim", "base_dn": "DC=klant,DC=nl", "preset": "ad"}


def test_new_domain_asks_the_mail_server(admin, helper):
    dom_id = _domain(admin)
    assert _sent(helper)["recipient_check"] == "callout"
    r = admin.get(f"/api/gateway/domains/{dom_id}/recipients").json()
    assert r["recipient_check"] == "callout" and r["destinations"][0]["host"] == "mail.klant.nl"
    listed = {d["name"]: d for d in admin.get("/api/gateway/domains").json()}
    assert listed["klant.nl"]["recipient_check"] == "callout"


def test_list(admin, helper):
    dom_id = _domain(admin)
    r = admin.put(f"/api/gateway/domains/{dom_id}/recipients",
                  json={"recipient_check": "list", "recipients": ["Info", "sales@klant.nl", "", "info@klant.nl"]})
    assert r.status_code == 200, r.text
    assert r.json()["recipients"] == ["info@klant.nl", "sales@klant.nl"]
    sent = _sent(helper)
    assert sent["recipient_check"] == "list" and sent["recipients"] == ["info@klant.nl", "sales@klant.nl"]
    bad = admin.put(f"/api/gateway/domains/{dom_id}/recipients", json={"recipient_check": "list", "recipients": ["a@other.nl"]})
    assert bad.status_code == 422
    p = bad.json()["detail"]["problems"][0]
    assert p["code"] == "recipient_invalid" and p["nl"]
    t = admin.post(f"/api/gateway/domains/{dom_id}/recipients/test", json={"address": "sales"}).json()
    assert t["exists"] is True and t["address"] == "sales@klant.nl"
    assert admin.post(f"/api/gateway/domains/{dom_id}/recipients/test", json={"address": "nope@klant.nl"}).json()["exists"] is False


def test_ldap_password_is_encrypted_and_kept(admin, helper):
    dom_id = _domain(admin)
    r = admin.put(f"/api/gateway/domains/{dom_id}/recipients", json={"recipient_check": "ldap", "ldap": LDAP})
    assert r.status_code == 200, r.text
    out = r.json()["ldap"]
    assert out["bind_password"] is None and out["has_password"] is True
    assert out["servers"] == ["ldap://dc1.klant.nl:389"]
    assert _sent(helper)["ldap"]["bind_password"] == "geheim"
    with session() as db:
        route = db.get(DomainRoute, dom_id)
        assert route.recipient_ldap_password and "geheim" not in route.recipient_ldap_password
        assert "bind_password" not in route.recipient_ldap
    # Saving again without a password keeps the saved one.
    r = admin.put(f"/api/gateway/domains/{dom_id}/recipients",
                  json={"recipient_check": "ldap", "ldap": {**LDAP, "bind_password": None, "base_dn": "OU=Users,DC=klant,DC=nl"}})
    assert r.status_code == 200, r.text
    assert _sent(helper)["ldap"]["bind_password"] == "geheim"
    # Switching to the mail server check keeps the LDAP settings for later.
    admin.put(f"/api/gateway/domains/{dom_id}/recipients", json={"recipient_check": "callout"})
    assert _sent(helper)["ldap"] is None
    assert admin.get(f"/api/gateway/domains/{dom_id}/recipients").json()["ldap"]["base_dn"] == "OU=Users,DC=klant,DC=nl"


def test_ldap_problems(admin, helper):
    dom_id = _domain(admin)
    r = admin.put(f"/api/gateway/domains/{dom_id}/recipients",
                  json={"recipient_check": "ldap", "ldap": {**LDAP, "base_dn": "", "bind_password": "a\nb"}})
    assert r.status_code == 422
    assert {p["code"] for p in r.json()["detail"]["problems"]} >= {"ldap_base_missing", "ldap_value_invalid"}


def test_customer_sees_and_tests_but_cannot_change(admin, customer, helper, monkeypatch):
    dom_id = _domain(admin)
    admin.put(f"/api/gateway/domains/{dom_id}/recipients", json={"recipient_check": "list", "recipients": ["info"]})
    assert customer.get(f"/api/gateway/domains/{dom_id}/recipients").json()["recipient_check"] == "list"
    assert customer.put(f"/api/gateway/domains/{dom_id}/recipients", json={"recipient_check": "off"}).status_code == 403
    # Customers always test the saved settings, never their own.
    t = customer.post(f"/api/gateway/domains/{dom_id}/recipients/test",
                      json={"address": "info", "settings": {"recipient_check": "off"}}).json()
    assert t["method"] == "list" and t["exists"] is True
    other = _domain(admin, "andere.nl")
    assert customer.get(f"/api/gateway/domains/{other}/recipients").status_code == 404


def test_test_unsaved_ldap_settings(admin, monkeypatch):
    dom_id = _domain(admin)
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        with open(cmd[-1].split(":", 1)[1]) as fh:
            seen["cf"] = fh.read()
        return subprocess.CompletedProcess(cmd, 0, "jan@klant.nl\n", "")

    monkeypatch.setattr(recipients, "ldap_lookup",
                        lambda d, a, runner=None, _orig=recipients.ldap_lookup: _orig(d, a, fake_run))
    r = admin.post(f"/api/gateway/domains/{dom_id}/recipients/test",
                   json={"address": "Jan@klant.nl", "settings": {"recipient_check": "ldap", "ldap": LDAP}})
    assert r.status_code == 200, r.text
    assert r.json()["exists"] is True
    assert seen["cmd"][:3] == ["postmap", "-q", "jan@klant.nl"]
    assert "bind_pw = geheim" in seen["cf"] and "domain = klant.nl" in seen["cf"]


def test_ldap_lookup_results():
    d = mfc.Domain("klant.nl", [mfc.Destination("m.klant.nl")], recipient_check="ldap",
                   ldap=mfc.LdapSource(servers=["ldap://dc1:389"], base_dn="DC=klant,DC=nl"))
    nf = recipients.ldap_lookup(d, "x@klant.nl", lambda c: subprocess.CompletedProcess(c, 1, "", ""))
    assert nf["exists"] is False
    err = recipients.ldap_lookup(d, "x@klant.nl", lambda c: subprocess.CompletedProcess(
        c, 1, "", "postmap: warning: dict_ldap_connect: Unable to bind to server ldap://dc1:389 with dn x: 49 (Invalid credentials)"))
    assert err["exists"] is None and "Invalid credentials" in err["detail"]


class FakeSmtp:
    def __init__(self, host, port, local_hostname=None, timeout=None, accept_all=False):
        self.accept_all = accept_all

    def ehlo_or_helo_if_needed(self): pass
    def has_extn(self, name): return False
    def mail(self, sender): return 250, b"ok"

    def rcpt(self, addr):
        if addr == "jan@klant.nl" or self.accept_all:
            return 250, b"2.1.5 Recipient OK"
        return 550, b"5.1.1 User unknown"

    def quit(self): pass


def test_callout():
    d = mfc.Domain("klant.nl", [mfc.Destination("mail.klant.nl")])
    r = recipients.test_address(d, "jan@klant.nl", "mf.rhis.nl", smtp_factory=FakeSmtp)
    assert r["exists"] is True and r["accepts_all"] is False and r["server"] == "mail.klant.nl:25"
    assert recipients.test_address(d, "piet@klant.nl", "mf.rhis.nl", smtp_factory=FakeSmtp)["exists"] is False
    lax = recipients.test_address(d, "piet@klant.nl", "mf.rhis.nl",
                                  smtp_factory=lambda *a, **k: FakeSmtp(*a, **k, accept_all=True))
    assert lax["exists"] is True and lax["accepts_all"] is True

    def down(*a, **k):
        raise OSError("Connection refused")
    r = recipients.test_address(d, "jan@klant.nl", "mf.rhis.nl", smtp_factory=down)
    assert r["exists"] is None and "refused" in r["detail"]


def test_cache_settings(admin, helper):
    _domain(admin)
    assert admin.get("/api/gateway/recipient-cache").json() == {"positive_days": 7, "negative_minutes": 180}
    r = admin.put("/api/gateway/recipient-cache", json={"positive_days": 14, "negative_minutes": 30})
    assert r.status_code == 200
    assert helper[-1]["recipient_cache"] == {"positive_days": 14, "negative_minutes": 30}
    assert admin.put("/api/gateway/recipient-cache", json={"positive_days": 0, "negative_minutes": 30}).status_code == 422


def test_cluster_snapshot_carries_ldap_password(admin, helper):
    dom_id = _domain(admin)
    admin.put(f"/api/gateway/domains/{dom_id}/recipients", json={"recipient_check": "ldap", "ldap": LDAP})
    with session() as db:
        snap = snapshot.export(db, with_dkim=False)
    route = next(d for d in snap["gateway"]["domains"] if d["name"] == "klant.nl")["route"]
    assert route["recipient_check"] == "ldap" and route["recipient_ldap_password"] == "geheim"
    with session() as db:
        r = db.get(DomainRoute, dom_id)
        snapshot._route_password_in(r, {"recipient_ldap_password": "nieuw"})
        assert recipients.decrypt(r.recipient_ldap_password) == "nieuw"
        before = r.recipient_ldap_password
        snapshot._route_password_in(r, {"recipient_ldap_password": "nieuw"})
        assert r.recipient_ldap_password == before  # unchanged: not re-encrypted
