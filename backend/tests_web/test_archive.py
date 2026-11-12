"""Mail archive: storing, hash chain, seals, tamper detection, retention, search and access."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from mailfilter import gateway
from mailfilter.archive import chain, maintain, store, usage_by_domain
from mailfilter.archive.ingest import archive_message
from mailfilter.archive.verify import verify
from mailfilter.db import get_engine, session
from mailfilter.models import ArchiveDomain, ArchiveMessage, ArchivePurge, ArchiveSeal, AuditLog, TraceMessage, TraceRecipient
from mailfilter_config import GatewayConfig, render

MAIL = b"""From: Klaver Hoveniers <boekhouding@klaver-hoveniers.nl>
To: info@klant.nl
Cc: Admin <administratie@klant.nl>
Subject: RE: Factuur 2026-0371 van De Vries Webshop
Date: Thu, 12 Nov 2026 10:41:07 +0100
Message-ID: <abc@klaver>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b1"

--b1
Content-Type: text/html; charset=utf-8

<p>Beste,</p><p>Hierbij de betaling van de <b>factuur</b> voor het snoeiwerk.</p><script>x()</script>
--b1
Content-Type: application/pdf
Content-Disposition: attachment; filename="factuur-2026-0371.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--b1--
"""
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def archive_root(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "root", lambda: tmp_path)
    calls = []

    def fake(config_json):
        calls.append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", fake)
    return tmp_path


def enable(name="klant.nl", years=7):
    with session() as db:
        db.add(ArchiveDomain(name=name, enabled=True, retention_years=years, last_seq=0, head=chain.ZERO))
        db.commit()


def put(domain="klant.nl", raw=MAIL, *, qid="Q1", sender="boekhouding@klaver-hoveniers.nl", now=T0):
    with session() as db:
        return archive_message(db, domain, raw, queue_id=qid, sender=sender, now=now)


def test_store_chain_and_search_fields(archive_root):
    enable()
    m1 = put()
    m2 = put(qid="Q2", sender="info@klant.nl", now=T0 + timedelta(minutes=5))
    assert (m1.seq, m2.seq) == (1, 2)
    assert m1.prev_hash == chain.ZERO and m2.prev_hash == m1.entry_hash
    assert (m1.direction, m2.direction) == ("in", "out")
    assert m1.subject.startswith("RE: Factuur") and m1.from_addr == "boekhouding@klaver-hoveniers.nl"
    assert m1.header_to == ["info@klant.nl", "administratie@klant.nl"]
    assert m1.attachments[0]["name"] == "factuur-2026-0371.pdf"
    assert m1.retain_until.isoformat() == "2033-11-30"
    assert store.read(archive_root, m1.path) == MAIL
    assert m1.path.startswith("klant.nl/2033-11/2026-11-10/")
    # Same queue ID again (Postfix retried after a lost exit code): stored once
    assert put().id == m1.id
    with session() as db:
        assert db.execute(select(ArchiveDomain.last_seq)).scalar_one() == 2
        hits = db.execute(text("SELECT seq FROM archive_message WHERE search @@ websearch_to_tsquery('simple', 'snoeiwerk')")).all()
        assert [h[0] for h in hits] == [1, 2]
        assert usage_by_domain(db) == {"klant.nl": (m1.stored_size + m2.stored_size, 2)}


def test_not_archived_when_off(archive_root):
    assert put() is None
    enable()
    with session() as db:
        db.get(ArchiveDomain, "klant.nl").enabled = False
        db.commit()
    assert put() is None


def test_database_refuses_changes():
    enable()
    m = put()
    with session() as db:
        db.execute(text("UPDATE archive_message SET envelope_to = '{info@klant.nl}' WHERE id = :i"), {"i": m.id})
        db.commit()  # envelope recipients may be filled in before sealing
    for sql in ("UPDATE archive_message SET subject = 'x' WHERE id = :i", "DELETE FROM archive_message WHERE id = :i"):
        with session() as db, pytest.raises(DBAPIError, match="archive_immutable"):
            db.execute(text(sql), {"i": m.id})


def test_seal_verify_and_tamper_detection(archive_root):
    enable()
    put()
    put(qid="Q2", now=T0 + timedelta(hours=2))
    put(qid="Q3", now=T0 + timedelta(days=1))
    with session() as db:
        db.add(TraceMessage(queue_id="Q1", first_seen=T0, last_seen=T0, status="delivered", done=True))
        db.add_all([TraceRecipient(queue_id="Q1", address="info@klant.nl", status="sent"),
                    TraceRecipient(queue_id="Q1", address="geheim@klant.nl", status="sent"),
                    TraceRecipient(queue_id="Q1", address="iemand@anderebedrijf.nl", status="sent")])
        db.commit()
        res = maintain.run(db, now=T0 + timedelta(days=2), ctl=lambda *a: {"message": "ok"})
        assert res.errors == [] and res.sealed == ["klant.nl 2026-11-10", "klant.nl 2026-11-11"]
        m1 = db.execute(select(ArchiveMessage).where(ArchiveMessage.seq == 1)).scalar_one()
        # Bcc recipient in this domain is kept; another company's recipient is not shown
        assert m1.envelope_to == ["geheim@klant.nl", "info@klant.nl"] and m1.sealed
        m1_path = m1.path
        seals = db.execute(select(ArchiveSeal).order_by(ArchiveSeal.day)).scalars().all()
        assert [s.count for s in seals] == [2, 1] and seals[1].prev_seal == seals[0].seal_hash
        rep = verify(db)
        assert rep.ok and rep.messages == 3 and rep.seals == 2, rep.problems
        # Sealed rows cannot even get recipients any more
        with pytest.raises(DBAPIError, match="archive_immutable"):
            db.execute(text("UPDATE archive_message SET envelope_to = '{}' WHERE seq = 1"))
        db.rollback()
    # Someone edits a file on disk
    path = archive_root / m1_path
    path.chmod(0o640)
    import gzip
    path.write_bytes(gzip.compress(MAIL.replace(b"snoeiwerk", b"tuinwerk")))
    # ... and someone with database superpowers rewrites a subject and a seal
    with get_engine().begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text("UPDATE archive_message SET sender = 'x@y.nl' WHERE seq = 2"))
        c.execute(text("UPDATE archive_seal SET count = 5 WHERE day = '2026-11-11'"))
    with session() as db:
        rep = verify(db)
        codes = {(p["seq"], p["code"]) for p in rep.problems}
        assert not rep.ok
        assert (1, "file_changed") in codes and (2, "entry_hash") in codes
        assert any(c == "seal_hash" for _, c in codes)


def test_retention_purge_keeps_chain_verifiable(archive_root):
    enable(years=1)
    put(now=T0 - timedelta(days=800))  # kept until 2025-09-30
    put(qid="Q2", now=T0 - timedelta(days=799))
    put(qid="Q3", now=T0)
    removed = []
    with session() as db:
        res = maintain.run(db, now=T0 + timedelta(days=1), ctl=lambda *a: removed.append(a) or {"message": "ok"})
        assert res.purged == {"klant.nl": 2}, res.errors
        assert ("purge", "klant.nl", "2025-09") in removed
        assert db.execute(select(ArchiveMessage.seq)).scalars().all() == [3]
        p = db.execute(select(ArchivePurge)).scalar_one()
        assert (p.through_seq, p.count) == (2, 2)
        assert verify(db, files=False).ok
        assert db.execute(select(AuditLog.action).where(AuditLog.action == "archive.purge")).scalar_one()


def test_legal_hold_stops_purge(archive_root):
    enable(years=1)
    put(now=T0 - timedelta(days=800))
    with session() as db:
        d = db.get(ArchiveDomain, "klant.nl")
        d.legal_hold = True
        db.commit()
        res = maintain.run(db, now=T0, ctl=lambda *a: {"message": "ok"})
        assert res.purged == {}
        assert db.execute(select(ArchiveMessage)).scalar_one()
        with pytest.raises(DBAPIError, match="legal hold"):
            db.execute(text("DELETE FROM archive_message"))


def test_retention_never_shrinks_for_later_mail():
    enable(years=10)
    put()
    with session() as db:
        db.get(ArchiveDomain, "klant.nl").retention_years = 1
        db.commit()
    m = put(qid="Q2")
    assert m.retain_until.isoformat() == "2036-11-30"


def test_render_archive_bcc():
    out = render(GatewayConfig(archive_domains=["klant.nl", "Rhis.nl"]))
    text_ = out.files["/etc/mailfilter/postfix/archive_bcc"]
    assert "@klant.nl klant.nl@archive.mailfilter.invalid" in text_
    assert "@rhis.nl rhis.nl@archive.mailfilter.invalid" in text_
    assert "/etc/mailfilter/postfix/archive_bcc" in out.hash_maps


def test_tracer_ignores_archive_copy():
    from mailfilter_tracer.parser import Event
    from mailfilter_tracer.tracker import Tracker

    t = Tracker()
    for to, relay in (("info@klant.nl", "mail.klant.nl[192.0.2.1]:25"), ("klant.nl@archive.mailfilter.invalid", "mfarchive")):
        m = t.apply(Event("delivery", T0, "smtp", "Q1", {"to": to, "relay": relay, "delay": "1", "dsn": "2.0.0",
                                                         "status": "sent", "reply": "ok"}))
    assert list(m.recipients) == ["info@klant.nl"] and m.status == "delivered"


# ---- web interface ---------------------------------------------------------------------
def test_admin_settings_apply_gateway(admin, archive_root):
    calls = []
    gateway.run_helper = lambda cfg: calls.append(json.loads(cfg)) or {"ok": True, "changed": []}
    r = admin.put("/api/archive/domains/klant.nl", json={"enabled": True, "retention_years": 7})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] and calls[-1]["archive_domains"] == ["klant.nl"]
    assert admin.put("/api/archive/domains/onbekend.nl", json={"enabled": True}).status_code == 404
    assert admin.put("/api/archive/domains/klant.nl", json={"enabled": True, "retention_years": 11}).status_code == 422
    r = admin.put("/api/archive/domains/klant.nl", json={"enabled": True, "retention_years": 10, "legal_hold": True,
                                                          "legal_hold_note": "Zaak 2026-14"})
    assert r.json()["legal_hold"] and r.json()["legal_hold_since"]
    st = admin.get("/api/archive/status").json()
    assert st["can_manage"] and st["public_key"] and len(st["key_id"]) == 16


def test_customer_scope_search_view_export(admin, customer, archive_root):
    enable("klant.nl")
    enable("rhis.nl")
    m = put()
    put("rhis.nl", MAIL.replace(b"snoeiwerk", b"onderhoud"), qid="Q9")
    assert customer.put("/api/archive/domains/klant.nl", json={"enabled": False}).status_code == 403
    r = customer.get("/api/archive/search", params={"q": "factuur"}).json()
    assert r["total"] == 1 and r["items"][0]["domain"] == "klant.nl"
    assert admin.get("/api/archive/search", params={"q": "factuur"}).json()["total"] == 2
    assert admin.get("/api/archive/search", params={"q": "snoeiwerk -onderhoud"}).json()["total"] == 1
    assert customer.get("/api/archive/search", params={"recipient": "administratie@"}).json()["total"] == 1
    assert customer.get("/api/archive/search", params={"direction": "out"}).json()["total"] == 0
    other = admin.get("/api/archive/search", params={"domain": "rhis.nl"}).json()["items"][0]["id"]
    assert customer.get(f"/api/archive/messages/{other}").status_code == 404
    d = customer.get(f"/api/archive/messages/{m.id}").json()
    assert d["intact"] and "snoeiwerk" in d["text"] and "x()" not in d["text"]
    raw = customer.get(f"/api/archive/messages/{m.id}/eml")
    assert raw.content == MAIL and raw.headers["x-archive-sha256"] == m.sha256
    z = customer.get("/api/archive/export", params={"q": "factuur"})
    assert z.status_code == 200, z.text
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = zf.namelist()
        assert "messages/klant.nl-00000001.eml" in names and "manifest.csv" in names
        man = json.loads(zf.read("manifest.json"))
        assert man["count"] == 1 and man["messages"][0]["sha256"] == m.sha256 and man["public_keys"] is not None
        assert zf.read("messages/klant.nl-00000001.eml") == MAIL
    st = customer.get("/api/archive/status").json()
    assert [x["name"] for x in st["domains"]] == ["klant.nl"] and st["messages"] == 1
    with session() as db:
        actions = set(db.execute(select(AuditLog.action)).scalars())
    assert {"archive.search", "archive.view", "archive.download", "archive.export"} <= actions


def test_verify_endpoint_admin_only(admin, customer, archive_root):
    enable()
    put()
    assert customer.post("/api/archive/verify", json={}).status_code == 403
    r = admin.post("/api/archive/verify", json={"files": True})
    assert r.status_code == 200
    import time

    for _ in range(50):
        st = admin.get("/api/archive/status").json()
        if st["verify"] and not st["verify_running"] and not st["verify"].get("running"):
            break
        time.sleep(0.1)
    assert st["verify"]["ok"] and st["verify"]["messages"] == 1
