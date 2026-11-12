from datetime import datetime, timedelta, timezone

import pytest

from mailfilter.db import session
from mailfilter.models import TraceMessage, TraceRecipient

NOW = datetime.now(timezone.utc)


def add(db, qid, status, minutes, rcpt="info@klant.nl", rstatus="sent", direction="inbound", sender="a@partner.nl", **kw):
    explain = kw.pop("explain", None)
    m = TraceMessage(queue_id=qid, first_seen=NOW - timedelta(minutes=minutes), last_seen=NOW - timedelta(minutes=minutes),
                     status=status, sender=sender, direction=direction, symbols={}, **kw)
    m.recipients = [TraceRecipient(queue_id=qid, address=rcpt, status=rstatus, explain=explain)]
    db.add(m)


@pytest.fixture
def flow(users, monkeypatch):
    monkeypatch.setattr("mailfilter.api.dashboard.services_down", lambda: ["clamav-daemon"])
    monkeypatch.setattr("mailfilter.api.dashboard.summary", lambda: {"deferred": 3, "hold": 1})
    with session() as db:
        add(db, "D1", "delivered", 30)
        add(db, "D2", "delivered", 90, rcpt="x@gmail.com", direction="outbound", sender="shop@rhis.nl")
        add(db, "S1", "quarantined", 40, rstatus="quarantined")
        add(db, "S2", "rejected", 50, rstatus="rejected", explain="rejected_blocklist")
        add(db, "R1", "rejected", 55, rstatus="rejected", explain="rejected_unknown_user")
        add(db, "W1", "deferred", 180, rcpt="jan@traag.nl", rstatus="deferred", direction="outbound", sender="shop@rhis.nl")
        add(db, "Y1", "delivered", 60 * 30)  # yesterday
        db.commit()


def test_counts_and_categories(admin, flow):
    d = admin.get("/api/dashboard").json()
    assert d["now"]["total"] == 6 and d["now"]["delivered"] == 2 and d["now"]["spam"] == 2
    assert d["now"]["refused"] == 1 and d["now"]["delayed"] == 1
    assert d["now"]["inbound"] == 4 and d["now"]["outbound"] == 2
    assert d["previous"]["total"] == 1
    assert len(d["series"]) == 24
    assert sum(b["delivered"] + b["spam"] + b["refused"] + b["delayed"] + b["failed"] for b in d["series"]) == 6
    assert d["top_domains"][0] == {"name": "klant.nl", "count": 4}
    assert d["top_senders"][0]["name"] == "a@partner.nl"


def test_month_and_direction(admin, flow):
    d = admin.get("/api/dashboard?range=30d&direction=outbound").json()
    assert d["now"]["total"] == 2 and len(d["series"]) in (30, 31)


def test_alerts(admin, flow):
    codes = {a["code"]: a for a in admin.get("/api/dashboard").json()["alerts"]}
    assert codes["stuck"]["params"]["domain"] == "traag.nl"
    assert codes["services_down"]["params"]["services"] == "clamav-daemon"
    assert codes["quarantine"]["params"]["count"] == 1


def test_customer_is_scoped(customer, flow):
    d = customer.get("/api/dashboard").json()
    assert d["now"]["total"] == 4 and d["queue"] is None
    codes = {a["code"] for a in d["alerts"]}
    assert "services_down" not in codes and "stuck" not in codes


def test_outbound_spike(admin, users, monkeypatch):
    monkeypatch.setattr("mailfilter.api.dashboard.services_down", lambda: [])
    monkeypatch.setattr("mailfilter.api.dashboard.summary", lambda: {})
    with session() as db:
        for i in range(120):
            add(db, f"O{i}", "delivered", 5 + i % 50, rcpt=f"u{i}@gmail.com", direction="outbound", sender="wp@rhis.nl", sasl_user="webshop@rhis.nl")
        db.commit()
    alerts = [a for a in admin.get("/api/dashboard").json()["alerts"] if a["code"] == "outbound_spike"]
    assert alerts and alerts[0]["params"]["source"] == "webshop@rhis.nl" and alerts[0]["level"] == "critical"


def test_live_minutes_and_feed(admin, flow):
    with session() as db:
        add(db, "L1", "delivered", 2)
        add(db, "L2", "deferred", 1, rcpt="jan@traag.nl", rstatus="deferred", direction="outbound", sender="shop@rhis.nl")
        db.commit()
    d = admin.get("/api/dashboard/live").json()
    assert len(d["minutes"]) == 60
    total = sum(b["delivered"] + b["spam"] + b["refused"] + b["delayed"] + b["failed"] for b in d["minutes"])
    assert total == 6  # L1, L2 and D1, S1, S2, R1 (30-55 minutes ago)
    assert [e["queue_id"] for e in d["events"][:2]] == ["L2", "L1"]
    assert d["events"][0]["category"] == "delayed" and d["events"][1]["category"] == "delivered"
    out = admin.get("/api/dashboard/live?direction=outbound").json()
    assert {e["queue_id"] for e in out["events"]} == {"L2", "D2", "W1"}


def test_live_customer_is_scoped(customer, flow):
    d = customer.get("/api/dashboard/live").json()
    assert {e["queue_id"] for e in d["events"]} == {"D1", "S1", "S2", "R1"}
