from datetime import datetime, timedelta, timezone

from mailfilter.db import session
from mailfilter.models import TraceMessage, TraceRecipient

NOW = datetime.now(timezone.utc)


def add(db, qid, sender, rcpt):
    m = TraceMessage(queue_id=qid, first_seen=NOW - timedelta(minutes=5), last_seen=NOW, status="delivered",
                     sender=sender, direction="inbound", symbols={})
    m.recipients = [TraceRecipient(queue_id=qid, address=rcpt, status="sent")]
    db.add(m)


def seed():
    with session() as db:
        add(db, "A1", "facturen@leverancier.nl", "info@klant.nl")
        add(db, "A2", "facturen@leverancier.nl", "boekhouding@rhis.nl")
        db.commit()


def test_admin_sees_everything(admin, users):
    seed()
    d = admin.get("/api/search?q=leverancier").json()
    assert {m["queue_id"] for m in d["messages"]} == {"A1", "A2"}
    assert admin.get("/api/search?q=klant").json()["domains"] == [{"name": "klant.nl"}]
    assert admin.get("/api/search?q=reinder").json()["users"][0]["email"] == "admin@rhis.nl"
    assert admin.get("/api/search?q=a1").json()["messages"][0]["queue_id"] == "A1"


def test_customer_is_scoped(customer, users):
    seed()
    d = customer.get("/api/search?q=leverancier").json()
    assert [m["queue_id"] for m in d["messages"]] == ["A1"]
    assert customer.get("/api/search?q=rhis").json()["domains"] == []
    assert customer.get("/api/search?q=admin").json()["users"] == []


def test_short_query_returns_nothing(admin, users):
    assert admin.get("/api/search?q=a").json() == {"messages": [], "domains": [], "users": []}
