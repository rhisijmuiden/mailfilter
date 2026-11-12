import json
from datetime import datetime, timedelta, timezone

import pytest

from mailfilter import digest
from mailfilter.db import session
from mailfilter.lists import ListError, norm_sender
from mailfilter.models import DigestSent
from mailfilter.settings import get_settings

from test_trace import queue, trace  # noqa: F401  (fixtures)


def read_map(name):
    return (get_settings().lists_dir / name).read_text().splitlines()[1:]


def test_normalise_sender():
    assert norm_sender(" Info@Shop.Example ") == "info@shop.example"
    assert norm_sender("@Shop.example") == "shop.example"
    assert norm_sender("bücher.example") == "xn--bcher-kva.example"
    for bad in ("*.shop.example", "nope", "a|b@x.nl", "a@b"):
        with pytest.raises(ListError):
            norm_sender(bad)


def test_customer_manages_own_lists(customer, admin):
    r = customer.post("/api/lists", json={"list": "allow", "recipient": "Jan@klant.nl", "sender": "@leverancier.nl", "comment": "facturen"})
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["recipient"], e["sender"], e["scope"], e["sender_kind"]) == ("jan@klant.nl", "leverancier.nl", "mailbox", "domain")
    assert customer.post("/api/lists", json={"list": "block", "recipient": "klant.nl", "sender": "spam@bad.example"}).status_code == 201
    assert read_map("allow.map") == ["jan@klant.nl|leverancier.nl"]
    assert read_map("block.map") == ["klant.nl|spam@bad.example"]
    # someone else's domain, and the list for every domain, are off limits
    assert customer.post("/api/lists", json={"list": "block", "recipient": "rhis.nl", "sender": "x@y.nl"}).json()["detail"] == "not_your_domain"
    assert customer.post("/api/lists", json={"list": "block", "recipient": "*", "sender": "x@y.nl"}).json()["detail"] == "everyone_admin_only"
    assert customer.post("/api/lists", json={"list": "block", "recipient": "nope.nl", "sender": "x@y.nl"}).status_code in (403, 422)
    # same pair again moves it to the other list
    r = customer.post("/api/lists", json={"list": "block", "recipient": "jan@klant.nl", "sender": "leverancier.nl"})
    assert r.json()["id"] == e["id"] and r.json()["list"] == "block"
    assert read_map("allow.map") == []
    # admin entries for everyone are invisible to the customer
    assert admin.post("/api/lists", json={"list": "allow", "recipient": "*", "sender": "partner.example"}).json()["scope"] == "everyone"
    assert len(customer.get("/api/lists").json()["items"]) == 2
    assert len(admin.get("/api/lists").json()["items"]) == 3
    assert read_map("allow.map") == ["*|partner.example"]
    everyone = admin.get("/api/lists?q=partner").json()["items"][0]["id"]
    assert customer.delete(f"/api/lists/{everyone}").status_code == 404
    assert customer.delete(f"/api/lists/{e['id']}").status_code == 204
    assert read_map("block.map") == ["klant.nl|spam@bad.example"]
    assert customer.get("/api/lists/options").json() == {"domains": ["klant.nl"], "everyone": False}


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr("mailfilter.mailer.send", lambda db, to, subject, text, html=None, **kw: out.append((to, subject, text, html)) or "<id>")
    return out


def test_report_run_and_links(admin, client, trace, queue, sent, monkeypatch):  # noqa: F811
    state, learned = queue
    monkeypatch.setattr("mailfilter.api.digest.learn", lambda kind, raw: learned.append((kind, raw)) or True)
    with session() as db:
        assert digest.run(db).reason == "disabled"
        digest.save_config(db, digest.ReportConfig(enabled=True, hour=0, language="nl"))
        db.commit()
        res = digest.run(db)
    # CCC333 for info@klant.nl and the virus DDD444 for admin@rhis.nl
    assert res.ran and res.sent == 2 and not res.failed
    by_rcpt = {to: (subject, text, html) for to, subject, text, html in sent}
    subject, text, html = by_rcpt["info@klant.nl"]
    assert "tegengehouden" in subject and "You won!" in text and "Vrijgeven of blokkeren" in html
    assert "alleen je beheerder" in by_rcpt["admin@rhis.nl"][1]
    # once a day
    with session() as db:
        assert digest.run(db).reason == "not_due"
    token = text.split("/report/")[1].split()[0]
    info = client.get(f"/api/report/{token}").json()
    assert info["recipient"] == "info@klant.nl" and info["subject"] == "You won!" and info["state"] == "held"
    assert client.get(f"/api/report/{token[:-2]}xx").status_code == 404
    r = client.post(f"/api/report/{token}", json={"action": "release_allow"})
    assert r.status_code == 200 and r.json()["listed"] is True
    assert json.loads(state.read_text())["log"] == [["release", "CCC333"]] and learned[-1][0] == "ham"
    assert read_map("allow.map") == ["info@klant.nl|win@lottery.example"]
    assert client.get(f"/api/report/{token}").json()["state"] == "gone"


def test_report_block_and_virus(client, trace, queue, sent, monkeypatch):  # noqa: F811
    state, learned = queue
    monkeypatch.setattr("mailfilter.api.digest.learn", lambda kind, raw: True)
    virus = digest.make_token("DDD444", "admin@rhis.nl")
    assert client.post(f"/api/report/{virus}", json={"action": "release"}).json()["detail"] == "virus_admin_only"
    assert client.post(f"/api/report/{virus}", json={"action": "block"}).status_code == 200
    assert read_map("block.map") == ["admin@rhis.nl|x@virus.example"]
    # a token for someone who is not a recipient of the message is refused
    assert client.get(f"/api/report/{digest.make_token('CCC333', 'admin@rhis.nl')}").status_code == 404
    old = digest.make_token("CCC333", "info@klant.nl", now=datetime.now(timezone.utc).timestamp() - 30 * 86400)
    assert client.get(f"/api/report/{old}").json()["detail"] == "expired"


def test_report_only_new_and_domain_switch(admin, customer, trace, queue, sent):  # noqa: F811
    r = customer.put("/api/quarantine-report/settings", json={"domains": [{"name": "klant.nl", "enabled": False}]})
    assert r.status_code == 200 and r.json()["config"] is None and r.json()["domains"] == [{"name": "klant.nl", "enabled": False}]
    assert customer.put("/api/quarantine-report/settings", json={"config": {"enabled": True}}).status_code == 403
    assert customer.put("/api/quarantine-report/settings", json={"domains": [{"name": "rhis.nl", "enabled": False}]}).status_code == 403
    assert admin.put("/api/quarantine-report/settings", json={"config": {"enabled": True, "hour": 0, "language": "en", "only_new": True}}).status_code == 200
    with session() as db:
        db.add(DigestSent(recipient="admin@rhis.nl", sent_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
        res = digest.run(db)
    assert res.sent == 0 and res.skipped == 1  # klant.nl is off, admin's virus mail is not new


def test_example_report(customer, trace, queue, sent):  # noqa: F811
    r = customer.post("/api/quarantine-report/example")
    assert r.status_code == 200 and r.json() == {"sent_to": "info@klant.nl", "messages": 1}
    assert sent[0][0] == "info@klant.nl" and "Quarantaineoverzicht" in sent[0][1]
