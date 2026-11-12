import json
from datetime import datetime, timedelta, timezone

import pytest

from mailfilter.db import session
from mailfilter.models import TraceMessage, TraceRecipient

NOW = datetime.now(timezone.utc)

RAW = (
    "From: Spammer <win@lottery.example>\r\nTo: info@klant.nl\r\nSubject: You won!\r\n"
    "Content-Type: text/html; charset=utf-8\r\n\r\n<p>Click <b>here</b></p><script>alert(1)</script>\r\n"
)


def msg(qid, sender, rcpts, status, minutes=10, **kw):
    m = TraceMessage(queue_id=qid, first_seen=NOW - timedelta(minutes=minutes), last_seen=NOW - timedelta(minutes=minutes - 1),
                     status=status, sender=sender, symbols=kw.pop("symbols", {}), **kw)
    m.recipients = [TraceRecipient(queue_id=qid, address=a, status=s, explain=e, reply=r, relay_host=h)
                    for a, s, e, r, h in rcpts]
    return m


@pytest.fixture
def trace(users):
    with session() as db:
        db.add_all([
            msg("AAA111", "shop@rhis.nl", [("piet@gmail.com", "sent", None, "250 ok", "gmail-smtp-in.l.google.com")],
                "delivered", direction="outbound", client_host="web01.rhis.nl", client_ip="203.0.113.25", spam_score=0.4),
            msg("BBB222", "boss@partner.nl", [("info@klant.nl", "bounced", "user_unknown",
                "host mx.klant.nl[192.0.2.1] said: 550 5.1.1 User unknown", "mx.klant.nl"),
                ("other@elders.nl", "sent", None, "250 ok", "mx.elders.nl")], "partly_delivered", direction="inbound"),
            msg("CCC333", "win@lottery.example", [("info@klant.nl", "quarantined", "quarantine", "", None)], "quarantined",
                direction="inbound", spam_score=11.2, symbols={"BAYES_SPAM": 5.1, "RBL_SPAMHAUS": 4.0, "DMARC_POLICY_ALLOW": -0.5}),
            msg("DDD444", "x@virus.example", [("admin@rhis.nl", "quarantined", "quarantine", "", None)], "quarantined",
                direction="inbound", symbols={"CLAM_VIRUS": 10.0}),
            msg("OLD555", "old@rhis.nl", [("a@b.nl", "sent", None, "", None)], "delivered", minutes=60 * 24 * 30),
        ])
        db.commit()


def test_admin_sees_all_customer_only_own(admin, customer, trace):
    assert admin.get("/api/trace").json()["total"] == 4  # OLD555 outside the default 7 days
    ids = {m["queue_id"] for m in customer.get("/api/trace").json()["items"]}
    assert ids == {"BBB222", "CCC333"}
    assert customer.get("/api/trace/AAA111").status_code == 404


def test_customer_does_not_see_other_recipients(customer, trace):
    m = customer.get("/api/trace/BBB222").json()
    assert [r["address"] for r in m["recipients"]] == ["info@klant.nl"]


def test_search_and_filters(admin, trace):
    assert [m["queue_id"] for m in admin.get("/api/trace?q=gmail.com").json()["items"]] == ["AAA111"]
    assert [m["queue_id"] for m in admin.get("/api/trace?q=203.0.113").json()["items"]] == ["AAA111"]
    assert [m["queue_id"] for m in admin.get("/api/trace?q=bbb222").json()["items"]] == ["BBB222"]
    assert admin.get("/api/trace?q=100%25").json()["total"] == 0
    assert {m["queue_id"] for m in admin.get("/api/trace?status=problem").json()["items"]} == {"BBB222"}
    assert admin.get("/api/trace?direction=outbound").json()["total"] == 1
    assert admin.get("/api/trace?hours=1000").json()["total"] == 5


def test_explanations_in_users_language(admin, customer, trace):
    nl = customer.get("/api/trace/BBB222").json()
    assert nl["explanation"]["title"] == "De mailbox bestaat niet."
    assert nl["recipients"][0]["reply"].startswith("550 5.1.1")
    en = admin.get("/api/trace/BBB222").json()
    assert en["explanation"]["title"] == "The mailbox does not exist."


def test_spam_report(admin, customer, trace):
    r = admin.get("/api/trace/CCC333").json()["report"]
    g = {x["id"]: x for x in r["groups"]}
    assert r["score"] == 11.2
    assert g["content"]["status"] == "bad" and g["reputation"]["status"] == "bad" and g["auth"]["status"] == "ok"
    nl = customer.get("/api/trace/CCC333").json()["report"]
    assert next(x for x in nl["groups"] if x["id"] == "content")["items"][0]["label"].startswith("Lijkt op mail")
    assert admin.get("/api/trace/BBB222").json()["report"] is None


def test_route(admin, trace):
    route = admin.get("/api/trace/AAA111").json()["route"]
    assert [n["kind"] for n in route] == ["source", "filter", "destination"]
    assert route[0]["name"] == "web01.rhis.nl" and route[2]["name"] == "gmail-smtp-in.l.google.com"
    assert route[2]["status"] == "sent"


def test_stats(admin, customer, trace):
    s = admin.get("/api/trace/stats").json()
    assert s["total"] == 4 and s["quarantined_now"] == 2 and s["outbound"] == 1
    assert customer.get("/api/trace/stats").json()["quarantined_now"] == 1


@pytest.fixture
def queue(tmp_path, monkeypatch):
    from mailfilter import settings

    state = tmp_path / "queue.json"
    state.write_text(json.dumps({"hold": ["CCC333", "DDD444"], "messages": {"CCC333": RAW, "DDD444": RAW}}))
    monkeypatch.setenv("FAKE_QUEUE", str(state))
    monkeypatch.setenv("MF_QUEUE_HELPER", str(__import__("pathlib").Path(__file__).parent / "fakes" / "fake-queuectl"))
    settings.get_settings.cache_clear()
    learned = []
    monkeypatch.setattr("mailfilter.api.quarantine.learn", lambda kind, raw: learned.append((kind, raw)) or True)
    yield state, learned
    settings.get_settings.cache_clear()


def test_quarantine_list_and_preview(customer, trace, queue):
    items = customer.get("/api/quarantine").json()["items"]
    assert [i["queue_id"] for i in items] == ["CCC333"]
    assert items[0]["reasons"][:2] == ["BAYES_SPAM", "RBL_SPAMHAUS"]
    p = customer.get("/api/quarantine/CCC333/preview").json()
    assert ["Subject", "You won!"] in p["headers"]
    assert "Click here" in p["text"] and "alert" not in p["text"]


def test_release_learns_ham(customer, trace, queue):
    state, learned = queue
    r = customer.post("/api/quarantine/CCC333/release", json={"learn": True})
    assert r.status_code == 200 and r.json()["learned"] == "ham"
    assert learned[0][0] == "ham" and b"You won!" in learned[0][1]
    assert json.loads(state.read_text())["log"] == [["release", "CCC333"]]
    assert customer.get("/api/quarantine").json()["items"] == []
    acts = customer.get("/api/trace/CCC333").json()["quarantine_actions"]
    assert acts[0]["action"] == "released" and acts[0]["user_email"] == "info@klant.nl"


def test_block_learns_spam(admin, trace, queue):
    _, learned = queue
    r = admin.post("/api/quarantine/CCC333/delete", json={"learn": True})
    assert r.json()["learned"] == "spam" and learned[0][0] == "spam"
    assert admin.post("/api/quarantine/CCC333/delete", json={"learn": False}).status_code == 409


def test_virus_release_is_admin_only(admin, customer, trace, queue):
    with session() as db:
        m = db.get(TraceMessage, "DDD444")
        m.recipients[0].address = "info@klant.nl"
        db.commit()
    assert customer.post("/api/quarantine/DDD444/release", json={"learn": False}).json()["detail"] == "virus_admin_only"
    assert admin.post("/api/quarantine/DDD444/release", json={"learn": False}).status_code == 200


def test_customer_cannot_touch_other_domains(customer, trace, queue):
    assert customer.post("/api/quarantine/DDD444/delete", json={}).status_code == 404


def test_forged_sender_does_not_expose_other_customers_mail(customer, users):
    """Spam to rhis.nl (another customer) with a forged klant.nl sender is not the
    customer's mail: they may not see, preview or release it. Their own mail to
    the outside world stays visible."""
    with session() as db:
        db.add_all([
            msg("FFF666", "ceo@klant.nl", [("admin@rhis.nl", "quarantined", "quarantine", "", None)], "quarantined",
                direction="inbound"),
            msg("GGG777", "info@klant.nl", [("piet@gmail.com", "sent", None, "250 ok", None)], "delivered",
                direction="outbound"),
        ])
        db.commit()
    ids = {m["queue_id"] for m in customer.get("/api/trace").json()["items"]}
    assert "FFF666" not in ids and "GGG777" in ids
    assert customer.get("/api/trace/FFF666").status_code == 404
    assert customer.post("/api/quarantine/FFF666/release", json={"learn": False}).status_code == 404
