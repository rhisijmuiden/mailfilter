from datetime import date, datetime, timedelta, timezone

import pytest

from mailfilter import monthly
from mailfilter.db import session
from mailfilter.models import MonthlyStats, TraceMessage, TraceRecipient, User

NOW = datetime.now(timezone.utc)
THIS = monthly.month_of(NOW)
LAST = monthly.add_months(THIS, -1)


def at(m: date, day: int = 10) -> datetime:
    return monthly.bounds(m)[0] + timedelta(days=day - 1, hours=12)


def add(db, qid, status, when, rcpt="info@klant.nl", direction="inbound", sender="a@partner.nl", symbols=None, explain=None, **kw):
    m = TraceMessage(queue_id=qid, first_seen=when, last_seen=when, status=status, sender=sender, direction=direction,
                     symbols=symbols or {}, **kw)
    rcpts = rcpt if isinstance(rcpt, list) else [rcpt]
    m.recipients = [TraceRecipient(queue_id=qid, address=r, status="sent", explain=explain) for r in rcpts]
    db.add(m)


@pytest.fixture
def flow(users):
    with session() as db:
        w = at(LAST)
        add(db, "D1", "delivered", w)
        add(db, "D2", "delivered", w, rcpt=["a@klant.nl", "b@klant.nl"])  # one message, counted once
        add(db, "D3", "delivered", w, rcpt="jan@rhis.nl")
        add(db, "S1", "quarantined", w)
        add(db, "S2", "rejected", w, explain="rejected_blocklist")
        add(db, "V1", "quarantined", w, symbols={"CLAM_VIRUS": {"score": 10}})
        add(db, "R1", "rejected", w, explain="rejected_unknown_user")
        add(db, "O1", "delivered", w, rcpt="x@gmail.com", direction="outbound", sender="shop@klant.nl")
        add(db, "O2", "rejected", w, rcpt="y@gmail.com", direction="outbound", sender="shop@klant.nl", spam_action="reject")
        add(db, "P1", "delivered", at(monthly.add_months(LAST, -1)))  # month before
        add(db, "T1", "delivered", at(THIS, 1) if NOW > at(THIS, 1) else NOW - timedelta(minutes=1))  # this month
        db.commit()


def test_count_per_domain(flow):
    with session() as db:
        s = monthly.count(db, LAST, ["klant.nl", "rhis.nl"])
    k = s["klant.nl"]
    assert (k.received, k.delivered, k.spam, k.virus, k.refused) == (6, 2, 2, 1, 1)
    assert (k.outbound, k.outbound_blocked) == (2, 1)
    assert k.stopped == 3 and k.stopped_percent == 50.0
    assert s["rhis.nl"].received == 1 and s["rhis.nl"].outbound == 0


def test_store_finished_months_and_read_back(flow):
    with session() as db:
        stored = monthly.store_finished(db, NOW)
        db.commit()
        assert LAST in stored and THIS not in stored
        assert monthly.store_finished(db, NOW) == []  # once
        row = db.query(MonthlyStats).filter_by(month=LAST, domain="klant.nl").one()
        assert row.received == 6 and row.virus == 1
        # Stored numbers win over the trace (which is pruned after 90 days).
        db.query(TraceMessage).delete()
        db.commit()
        assert monthly.stats_for(db, LAST, ["klant.nl"])["klant.nl"].received == 6


def test_customer_sees_own_report(customer, flow):
    r = customer.get(f"/api/monthly-report?month={LAST:%Y-%m}").json()
    assert r["total"]["received"] == 6 and r["total"]["virus"] == 1 and r["previous"]["received"] == 1
    assert [d["name"] for d in r["domains"]] == ["klant.nl"]
    assert r["customers"] == [] and r["current"] is False
    assert len(r["history"]) == 12 and r["history"][-1]["month"] == f"{LAST:%Y-%m}"
    # Customers cannot look at someone else or at everything.
    assert customer.get(f"/api/monthly-report?month={LAST:%Y-%m}&customer=1").json()["total"]["received"] == 6
    assert customer.get("/api/monthly-report?month=2999-01").status_code == 422
    assert customer.put("/api/monthly-report/settings", json={"config": {"enabled": True}}).status_code == 403


def test_admin_overview_and_per_customer(admin, flow):
    r = admin.get(f"/api/monthly-report?month={LAST:%Y-%m}").json()
    assert r["total"]["received"] == 7 and r["customer"] is None
    assert r["customers"][0]["email"] == "info@klant.nl" and r["customers"][0]["stats"]["received"] == 6
    with session() as db:
        cid = db.query(User).filter_by(email="info@klant.nl").one().id
    r = admin.get(f"/api/monthly-report?month={LAST:%Y-%m}&customer={cid}").json()
    assert r["total"]["received"] == 6 and r["customer"] == cid and r["customers"] == []
    assert admin.get("/api/monthly-report?customer=1").status_code == 404  # an admin, not a customer


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr("mailfilter.mailer.send", lambda db, to, subject, text, html=None, **kw: out.append((to, subject, text, html)) or "<id>")
    return out


def test_settings_and_run(admin, customer, flow, sent):
    with session() as db:
        r = monthly.run(db, NOW)
    assert not r.ran and r.reason == "disabled" and f"{LAST:%Y-%m}" in r.stored
    s = admin.put("/api/monthly-report/settings", json={"config": {"enabled": True, "day": 1, "hour": 0, "admin_summary": True}}).json()
    assert s["config"]["enabled"] and s["recipients"][0]["enabled"]

    with session() as db:
        r = monthly.run(db, NOW)
    assert r.ran and r.month == f"{LAST:%Y-%m}" and r.sent == 2 and not r.failed
    to = {x[0]: x for x in sent}
    nl = to["info@klant.nl"]
    assert nl[1].startswith("Je mailrapport over") and "Spam tegengehouden: 2" in nl[2] and "Virussen tegengehouden: 1" in nl[2]
    assert "#FF6B57" in nl[3]
    assert to["admin@rhis.nl"][1].startswith("Mail report for all customers")
    with session() as db:
        assert not monthly.run(db, NOW).ran  # once a month
    assert len(sent) == 2

    # Switched off for this customer: only the admin overview goes out.
    s = customer.put("/api/monthly-report/settings", json={"me_enabled": False}).json()
    assert s["me_enabled"] is False and s["config"] is None
    sent.clear()
    with session() as db:
        r = monthly.run(db, NOW, force=True)
    assert [x[0] for x in sent] == ["admin@rhis.nl"] and r.skipped == 1


def test_not_due_before_day(admin, flow):
    cfg = monthly.MonthlyConfig(enabled=True, day=5, hour=8)
    first = datetime.combine(THIS, datetime.min.time(), monthly.tz()).replace(day=3, hour=9)
    assert not monthly.due(cfg, None, first)
    assert monthly.due(cfg, None, first.replace(day=5))
    assert not monthly.due(cfg, f"{LAST:%Y-%m}", first.replace(day=5))


def test_send_example(customer, flow, sent):
    r = customer.post("/api/monthly-report/example", json={"month": f"{LAST:%Y-%m}"}).json()
    assert r["sent_to"] == "info@klant.nl" and len(sent) == 1


def test_render_english_numbers():
    rep = monthly.Report(month=date(2026, 10, 1), title="Klant", domains={"klant.nl": monthly.Stats(received=12345, spam=2000, virus=3)},
                         previous=monthly.Stats(received=10000))
    subject, text, html = monthly.render(rep, "en", "Jan")
    assert subject == "Your mail report for October 2026"
    assert "Mail received: 12,345 (+23% compared to September 2026)" in text
    _, text_nl, _ = monthly.render(rep, "nl", "Jan")
    assert "Mail ontvangen: 12.345" in text_nl and "16,2%" in text_nl
