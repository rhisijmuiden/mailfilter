"""Spam / not-spam reports from end users: reading the report mail (as
attachment, Microsoft's Report button, plain forwards from phones), finding the
original in the mail trace, who may report, learning, and the web interface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from sqlalchemy import select

from mailfilter.api import feedback as feedback_api
from mailfilter.db import session
from mailfilter.feedback import ingest
from mailfilter.feedback.parse import kind_from_subject, parse_inline, parse_report
from mailfilter.models import SenderListEntry, SpamFeedback, TraceMessage, TraceRecipient

NOW = datetime.now(timezone.utc)


def original(mid="abc123@spam.example", sender="promo@spam.example", to="jan@klant.nl", subject="Cheap pills") -> EmailMessage:
    m = EmailMessage()
    m["From"] = f"Promo <{sender}>"
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = f"<{mid}>"
    m["Date"] = "Wed, 12 Nov 2026 10:15:00 +0000"
    m.set_content("Buy cheap pills now, limited offer, click here")
    return m


def as_attachment(orig: EmailMessage, reporter="jan@klant.nl", to="spam@mailfilter.rhis.nl", subject="FW: Cheap pills") -> bytes:
    r = EmailMessage()
    r["From"] = f"Jan <{reporter}>"
    r["To"] = to
    r["Subject"] = subject
    r.set_content("This is spam")
    r.add_attachment(orig)
    return r.as_bytes()


def inline(body: str, reporter="jan@klant.nl", subject="Fwd: Cheap pills") -> bytes:
    r = EmailMessage()
    r["From"] = reporter
    r["To"] = "spam@mailfilter.rhis.nl"
    r["Subject"] = subject
    r.set_content(body)
    return r.as_bytes()


IPHONE_NL = """Verstuurd vanaf mijn iPhone

Begin doorgestuurd bericht:

Van: Promo <promo@spam.example>
Datum: 12 november 2026 om 11:15:00 CET
Aan: jan@klant.nl
Onderwerp: Cheap pills

Buy cheap pills now, limited offer
"""

OUTLOOK_EN = """
________________________________
From: Promo <promo@spam.example>
Sent: Wednesday, November 12, 2026 10:15 AM
To: Jan <jan@klant.nl>
Subject: Cheap pills

Buy cheap pills now
"""


def add_trace(qid="4ABC1", mid="abc123@spam.example", sender="promo@spam.example", rcpt="jan@klant.nl", status="sent",
              when=None, score=6.2):
    with session() as db:
        db.add(TraceMessage(queue_id=qid, first_seen=when or NOW - timedelta(hours=2), last_seen=NOW, direction="inbound",
                            status="delivered", message_id=mid, sender=sender, spam_score=score, spam_action="add header",
                            symbols={}, symbol_options={}))
        db.flush()
        db.add(TraceRecipient(queue_id=qid, address=rcpt, status=status))
        db.commit()


class Calls:
    def __init__(self, verified="dmarc", learn_ok=True):
        self.verified, self.learn_ok, self.learned = verified, learn_ok, []

    def check(self, raw, client, helo, sender, reporter):
        return self.verified

    def learn(self, kind, raw):
        self.learned.append((kind, raw))
        return self.learn_ok


def run(raw, calls=None, **kw):
    calls = calls or Calls()
    with session() as db:
        fb = ingest.handle(db, raw, checker=calls.check, learner=calls.learn, **kw)
        db.expunge(fb)
    return fb, calls


# --- parsing -----------------------------------------------------------------

def test_parse_attachment():
    rep = parse_report(as_attachment(original()))
    assert rep.reporter == "jan@klant.nl"
    assert rep.original.source == "attachment"
    assert rep.original.message_id == "<abc123@spam.example>"
    assert rep.original.sender == "promo@spam.example"
    assert b"Buy cheap pills" in rep.original.raw


def test_parse_eml_file_attachment():
    r = EmailMessage()
    r["From"] = "jan@klant.nl"
    r["Subject"] = "spam"
    r.set_content("see attached")
    r.add_attachment(original().as_bytes(), maintype="application", subtype="octet-stream", filename="Cheap pills.eml")
    rep = parse_report(r.as_bytes())
    assert rep.original.source == "attachment" and rep.original.sender == "promo@spam.example"


@pytest.mark.parametrize("body", [IPHONE_NL, OUTLOOK_EN])
def test_parse_inline_forwards(body):
    o = parse_inline(body)
    assert o.source == "inline"
    assert o.sender == "promo@spam.example"
    assert o.subject == "Cheap pills"
    assert o.date and o.date.date().isoformat() == "2026-11-12"
    assert b"Buy cheap pills" in o.raw


def test_kind_from_subject():
    assert kind_from_subject("Not junk: hello") == "ham"
    assert kind_from_subject("Geen spam: factuur") == "ham"
    assert kind_from_subject("Phishing: login") == "spam"
    assert kind_from_subject("FW: Spam tips") is None


def test_verdict_from_symbols():
    v = ingest.verdict_from_symbols
    assert v({"DMARC_POLICY_ALLOW": {}}, "klant.nl", "") == "dmarc"
    assert v({"R_DKIM_ALLOW": {"options": ["klant.nl:s=selector1"]}}, "klant.nl", "") == "dkim"
    assert v({"R_DKIM_ALLOW": {"options": ["klant.onmicrosoft.com:s=selector1"]}}, "klant.nl", "") == ""
    assert v({"R_SPF_ALLOW": {}}, "klant.nl", "bounce@mail.klant.nl") == "spf"
    assert v({"R_SPF_ALLOW": {}}, "klant.nl", "x@other.example") == ""


# --- handling ----------------------------------------------------------------

def test_learns_verified_report(users):
    add_trace()
    fb, calls = run(as_attachment(original()), rcpt_user="spam")
    assert fb.status == "learned" and fb.learned == "spam" and fb.kind == "spam"
    assert fb.queue_id == "4ABC1" and fb.recipient == "jan@klant.nl" and fb.score == 6.2
    assert fb.verified == "dmarc"
    assert calls.learned[0][0] == "spam" and b"Buy cheap pills" in calls.learned[0][1]


def test_not_spam_address_and_microsoft_not_junk(users):
    add_trace()
    fb, _ = run(as_attachment(original()), rcpt_user="notspam")
    assert fb.kind == "ham" and fb.status == "learned"
    add_trace(qid="4ABC2", mid="m2@x.example")
    fb, _ = run(as_attachment(original(mid="m2@x.example"), subject="Not junk: Cheap pills"), rcpt_user="spam")
    assert fb.kind == "ham"


def test_unverified_waits_then_customer_learns(users, customer, monkeypatch):
    add_trace()
    fb, calls = run(as_attachment(original()), calls=Calls(verified=""))
    assert fb.status == "unverified" and not calls.learned
    learned = []
    monkeypatch.setattr(feedback_api, "rspamd_learn", lambda kind, raw: learned.append(kind) or True)
    r = customer.post(f"/api/spam-reports/{fb.id}/learn")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "learned" and r.json()["handled_by"] == "info@klant.nl"
    assert learned == ["spam"]


def test_require_auth_off(users, admin):
    add_trace()
    r = admin.put("/api/spam-reports/settings", json={"enabled": True, "require_auth": False, "auto_block": False, "per_hour": 30})
    assert r.status_code == 200
    fb, _ = run(as_attachment(original()), calls=Calls(verified=""))
    assert fb.status == "learned"


def test_outsider_and_unknown_messages(users):
    add_trace()
    fb, calls = run(as_attachment(original(), reporter="evil@attacker.example"))
    assert fb.status == "not_customer" and not calls.learned
    # A message Mailfilter never delivered to klant.nl
    fb, calls = run(as_attachment(original(mid="made-up@x.example")))
    assert fb.status == "not_found" and not calls.learned
    # Delivered to rhis.nl, reported by klant.nl
    add_trace(qid="4ABC3", mid="other@x.example", rcpt="piet@rhis.nl")
    fb, _ = run(as_attachment(original(mid="other@x.example")))
    assert fb.status == "not_found"
    # Refused message (never reached the mailbox)
    add_trace(qid="4ABC4", mid="refused@x.example", status="bounced")
    fb, _ = run(as_attachment(original(mid="refused@x.example")))
    assert fb.status == "not_found"


def test_inline_forward_from_phone(users):
    add_trace(when=datetime(2026, 11, 12, 10, 15, 3, tzinfo=timezone.utc))
    fb, calls = run(inline(IPHONE_NL), now=datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc))
    assert fb.source == "inline" and fb.status == "learned" and fb.queue_id == "4ABC1"
    assert b"Buy cheap pills" in calls.learned[0][1]


def test_duplicate_and_rate_limit(users, admin):
    add_trace()
    run(as_attachment(original()))
    fb, calls = run(as_attachment(original()))
    assert fb.status == "duplicate" and not calls.learned
    admin.put("/api/spam-reports/settings", json={"enabled": True, "require_auth": True, "auto_block": False, "per_hour": 2})
    fb, _ = run(as_attachment(original()))
    assert fb.status == "rate_limited"


def test_disabled(users, admin):
    add_trace()
    admin.put("/api/spam-reports/settings", json={"enabled": False, "require_auth": True, "auto_block": False, "per_hour": 30})
    fb, calls = run(as_attachment(original()))
    assert fb.status == "disabled" and not calls.learned


def test_no_message(users):
    fb, _ = run(inline("Hi, I think this is spam but I forgot to forward it."))
    assert fb.status == "no_message"


def test_auto_block_and_undo(users, admin, customer, monkeypatch):
    add_trace()
    admin.put("/api/spam-reports/settings", json={"enabled": True, "require_auth": True, "auto_block": True, "per_hour": 30})
    fb, _ = run(as_attachment(original()))
    assert fb.status == "learned" and fb.blocked
    with session() as db:
        e = db.execute(select(SenderListEntry)).scalar_one()
        assert (e.list, e.recipient, e.sender) == ("block", "jan@klant.nl", "promo@spam.example")
    learned = []
    monkeypatch.setattr(feedback_api, "rspamd_learn", lambda kind, raw: learned.append(kind) or True)
    r = customer.post(f"/api/spam-reports/{fb.id}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "undone" and body["learned"] == "ham" and not body["blocked"] and body["can_learn"]
    assert learned == ["ham"]
    with session() as db:
        assert db.execute(select(SenderListEntry)).first() is None


def test_block_button(users, customer):
    add_trace()
    fb, _ = run(as_attachment(original()))
    r = customer.post(f"/api/spam-reports/{fb.id}/block")
    assert r.status_code == 200, r.text
    assert r.json()["blocked"] and not r.json()["can_block"]


def test_list_scoping_and_stats(users, admin, customer):
    add_trace()
    add_trace(qid="9X", mid="r@x.example", rcpt="piet@rhis.nl")
    run(as_attachment(original()))
    run(as_attachment(original(mid="r@x.example", to="piet@rhis.nl"), reporter="piet@rhis.nl"), calls=Calls(verified=""))
    run(as_attachment(original(), reporter="evil@attacker.example"))
    a = admin.get("/api/spam-reports").json()
    assert a["total"] == 3
    assert a["stats"] == {"spam": 3, "ham": 0, "learned": 1, "waiting": 1, "refused": 1}
    c = customer.get("/api/spam-reports").json()
    assert [i["reporter"] for i in c["items"]] == ["jan@klant.nl"]
    assert c["items"][0]["can_undo"] and not c["items"][0]["can_learn"]
    other = [i for i in a["items"] if i["reporter"] == "piet@rhis.nl"][0]
    assert customer.post(f"/api/spam-reports/{other['id']}/learn").status_code == 404
    assert customer.put("/api/spam-reports/settings", json={"enabled": False}).status_code == 403
    s = customer.get("/api/spam-reports/settings").json()
    assert s["spam_address"].startswith("spam@") and s["ham_address"].startswith("notspam@")


def test_raw_copy_is_dropped_after_a_while(users):
    add_trace()
    fb, _ = run(as_attachment(original()))
    with session() as db:
        ingest.cleanup(db, NOW + timedelta(days=ingest.KEEP_RAW_DAYS + 1))
        db.commit()
        assert db.execute(select(SpamFeedback.raw).where(SpamFeedback.id == fb.id)).scalar_one() is None
        ingest.cleanup(db, NOW + timedelta(days=ingest.KEEP_DAYS + 1))
        db.commit()
        assert db.execute(select(SpamFeedback)).first() is None


def test_pipe_main(users, monkeypatch, capsys):
    import io
    import sys

    add_trace()
    monkeypatch.setattr(ingest, "rspamd_check", lambda *a: "dkim")
    monkeypatch.setattr(ingest, "rspamd_learn", lambda kind, raw: True)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(as_attachment(original()))))
    assert ingest.main(["--to=spam", "--sender=jan@klant.nl", "--client=[2001:db8::1]", "--helo=mx.klant.nl"]) == 0
    assert "learned" in capsys.readouterr().out
