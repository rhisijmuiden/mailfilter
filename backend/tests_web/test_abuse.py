import json
from datetime import datetime, timedelta, timezone

import pytest

from mailfilter import abuse, gateway, mailer, queuectl
from mailfilter.db import session
from mailfilter.models import OutboundHold, RelayNetwork, TraceMessage

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    state = {"calls": [], "mails": [], "released": [], "deleted": []}

    def helper(config_json):
        state["calls"].append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", helper)
    monkeypatch.setattr(mailer, "send", lambda db, to, subject, text, html=None: state["mails"].append((to, subject, text)))
    monkeypatch.setattr(queuectl, "release", lambda qid: state["released"].append(qid))
    monkeypatch.setattr(queuectl, "delete", lambda qid: state["deleted"].append(qid))
    return state


def add_mail(n, *, ip="203.0.113.25", sasl=None, spam=0, minutes=1, status="sent", prefix="Q"):
    with session() as db:
        for i in range(n):
            t = NOW - timedelta(minutes=minutes)
            db.add(TraceMessage(queue_id=f"{prefix}{minutes}x{i}", first_seen=t, last_seen=t, direction="outbound",
                                status=status, client_ip=ip, sasl_user=sasl, symbols={},
                                spam_action="add header" if i < spam else "no action"))
        db.commit()


def scan():
    with session() as db:
        return [(h.kind, h.value, h.reason) for h in abuse.scan(db, NOW)]


def test_spam_burst_pauses_ip_and_alerts(users, fakes):
    add_mail(40, spam=15)
    assert scan() == [("ip", "203.0.113.25", "spam")]
    holds = fakes["calls"][-1]["holds"]
    assert holds == [{"kind": "ip", "value": "203.0.113.25", "reason": "15 of 40 messages were spam in 15 minutes"}]
    to, subject, text = fakes["mails"][-1]
    assert "admin@rhis.nl" in to and "203.0.113.25" in text and "gepauzeerd" in text
    assert scan() == []  # already paused


def test_volume_spike_pauses_account(users, fakes):
    add_mail(5, sasl="Webshop@Klant.nl", minutes=60 * 24, prefix="B")  # usual: almost nothing
    add_mail(400, sasl="Webshop@Klant.nl")
    assert scan() == [("account", "webshop@klant.nl", "volume")]


def test_normal_traffic_local_mail_and_exempt_are_left_alone(users, fakes):
    add_mail(400, ip="127.0.0.1", spam=400)  # the Mailfilter's own mail
    add_mail(50, ip="198.51.100.7", spam=3, prefix="N")  # a few spam messages is normal
    add_mail(40, ip="192.0.2.9", spam=40, prefix="E")
    with session() as db:
        s = abuse.load_settings(db)
        s.exempt = ["192.0.2.9"]
        abuse.save_settings(db, s)
        db.commit()
    assert scan() == []
    assert fakes["calls"] == [] and fakes["mails"] == []


def test_release_delivers_held_mail_and_starts_quiet_period(admin, fakes):
    add_mail(40, spam=15)
    scan()
    add_mail(3, status="held", prefix="H")
    holds = admin.get("/api/gateway/holds").json()
    assert holds[0]["value"] == "203.0.113.25" and holds[0]["held_messages"] == 3
    r = admin.post(f"/api/gateway/holds/{holds[0]['id']}/release", json={"held_mail": "deliver"})
    assert r.status_code == 200, r.text
    assert r.json()["done"] == 3 and sorted(fakes["released"]) == ["H1x0", "H1x1", "H1x2"]
    assert fakes["calls"][-1]["holds"] == []
    assert admin.post(f"/api/gateway/holds/{holds[0]['id']}/release", json={}).status_code == 409
    assert scan() == []  # quiet period after a release


def test_release_can_delete_held_mail(admin, fakes):
    add_mail(40, spam=15)
    scan()
    add_mail(2, status="held", prefix="H")
    hid = admin.get("/api/gateway/holds").json()[0]["id"]
    admin.post(f"/api/gateway/holds/{hid}/release", json={"held_mail": "delete"})
    assert sorted(fakes["deleted"]) == ["H1x0", "H1x1"] and fakes["released"] == []


def test_manual_pause_and_dashboard_alert(admin, fakes):
    r = admin.post("/api/gateway/holds", json={"kind": "ip", "value": "2001:DB8::1"})
    assert r.status_code == 201, r.text
    assert r.json()["value"] == "2001:db8::1" and r.json()["reason"] == "manual"
    assert fakes["calls"][-1]["holds"][0]["reason"] == "paused by an administrator"
    assert admin.post("/api/gateway/holds", json={"kind": "ip", "value": "2001:db8::1"}).status_code == 409
    assert admin.post("/api/gateway/holds", json={"kind": "ip", "value": "nope"}).status_code == 422
    codes = [a["code"] for a in admin.get("/api/dashboard").json()["alerts"]]
    assert "paused_senders" in codes


def test_customer_sees_own_pauses_only_and_cannot_release(admin, customer, fakes):
    with session() as db:
        from mailfilter.models import Domain
        klant = db.query(Domain).filter_by(name="klant.nl").one()
        db.add(RelayNetwork(cidr="203.0.113.0/24", domain_id=klant.id))
        db.add(OutboundHold(kind="ip", value="203.0.113.25", reason="manual", detail={}, automatic=False))
        db.add(OutboundHold(kind="ip", value="198.51.100.1", reason="manual", detail={}, automatic=False))
        db.add(OutboundHold(kind="account", value="shop@klant.nl", reason="manual", detail={}, automatic=False))
        db.commit()
    seen = sorted(h["value"] for h in customer.get("/api/gateway/holds").json())
    assert seen == ["203.0.113.25", "shop@klant.nl"]
    hid = customer.get("/api/gateway/holds").json()[0]["id"]
    assert customer.post(f"/api/gateway/holds/{hid}/release", json={}).status_code == 403
    assert customer.post("/api/gateway/holds", json={"kind": "ip", "value": "1.2.3.4"}).status_code == 403
    assert customer.get("/api/gateway/abuse-settings").status_code == 403


def test_settings_roundtrip(admin):
    s = admin.get("/api/gateway/abuse-settings").json()
    assert s["enabled"] is True and s["window_minutes"] == 15
    r = admin.put("/api/gateway/abuse-settings", json={**s, "enabled": False, "exempt": [" 10.0.0.1 ", "", "A@B.nl"]})
    assert r.status_code == 200, r.text
    assert r.json()["exempt"] == ["10.0.0.1", "a@b.nl"]
    assert admin.get("/api/gateway/abuse-settings").json()["enabled"] is False
    assert admin.put("/api/gateway/abuse-settings", json={**s, "spam_percent": 0}).status_code == 422
