"""Notifications: settings, channels, the check round (fire, grace, repeat, resolve) and the checks."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from mailfilter import mailer, notify
from mailfilter.db import session
from mailfilter.settings_store import get_setting

NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def sent(monkeypatch):
    out = {"mails": [], "http": []}
    monkeypatch.setattr(mailer, "send", lambda db, to, subject, text, html=None: out["mails"].append((to, subject, text)))

    def handler(request: httpx.Request) -> httpx.Response:
        out["http"].append(request)
        if "api.telegram.org" in str(request.url):
            if "/bot999:" in str(request.url):
                return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
            if str(request.url).endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [
                    {"message": {"chat": {"id": -100123, "title": "Ops", "type": "group"}}},
                    {"message": {"chat": {"id": 42, "first_name": "Reinder", "type": "private"}}},
                ]})
            return httpx.Response(200, json={"ok": True, "result": {}})
        if "fail" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(204)

    monkeypatch.setattr(notify, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return out


def settings(**kw) -> notify.NotifySettings:
    return notify.NotifySettings(**kw)


def save(**kw):
    with session() as db:
        notify.save_settings(db, settings(**kw))
        db.commit()


def run(alerts, now):
    with session() as db:
        res = notify.run_checks(db, now=now, collector=lambda s, n: list(alerts))
        db.commit()
        return res


def svc(name="postfix"):
    return notify.Alert("services", "services", f"services:{name}", "critical", {"service": name, "state": "failed"})


def test_defaults_send_everything_by_mail_to_admins(users, sent):
    with session() as db:
        s = notify.load_settings(db)
        assert s.email_enabled and not s.telegram_enabled
        assert all(s.types[t].channels == ["email", "telegram", "webhook"] for t in notify.TYPES)
        assert s.types["queue"].threshold == 200
        assert notify.email_recipients(db, s) == ["admin@rhis.nl"]


def test_condition_fires_once_repeats_and_resolves(users, sent):
    save(repeat_hours=6)
    q = notify.Alert("queue", "queue", "queue", "warning", {"deferred": 250, "active": 1, "hold": 0, "threshold": 200})
    assert [a.key for a in run([q], NOW)] == ["queue"]
    assert sent["mails"][-1][0] == ["admin@rhis.nl"] and "250" in sent["mails"][-1][2]
    assert run([q], NOW + dt.timedelta(hours=1)) == []
    assert [a.key for a in run([q], NOW + dt.timedelta(hours=7))] == ["queue"]
    res = run([], NOW + dt.timedelta(hours=8))
    assert [(a.key, a.event) for a in res] == [("queue", "resolved")]
    assert "Resolved" in sent["mails"][-1][1]
    assert len(sent["mails"]) == 3
    with session() as db:
        assert notify.active(db) == []
        assert [h["event"] for h in notify.history(db)] == ["resolved", "firing", "firing"]


def test_services_have_a_grace_period(users, sent):
    save()
    assert run([svc()], NOW) == []
    assert run([svc()], NOW + dt.timedelta(minutes=1)) == []
    assert run([], NOW + dt.timedelta(minutes=2)) == []  # restarted quickly: never announced, never resolved
    assert sent["mails"] == []
    run([svc()], NOW + dt.timedelta(minutes=10))
    assert [a.key for a in run([svc()], NOW + dt.timedelta(minutes=14))] == ["services:postfix"]


def test_channel_matrix_and_languages(users, sent):
    types = {t: {"channels": []} for t in notify.TYPES}
    types["blacklist"] = {"channels": ["telegram", "webhook"]}
    save(language="nl", telegram_enabled=True, telegram_chat_id="42", webhook_enabled=True,
         webhook_url="https://hooks.example/x", types=types)
    with session() as db:
        notify.save_settings(db, notify.load_settings(db), telegram_token="123:abc", webhook_secret="s3cret")
        db.commit()
    bl = notify.Alert("blacklist", "blacklist", "blacklist:203.0.113.25:zen", "critical",
                      {"ip": "203.0.113.25", "zone": "zen.spamhaus.org", "name": "Spamhaus ZEN", "delist": "https://x"})
    run([bl, svc()], NOW + dt.timedelta(hours=1))
    run([bl, svc()], NOW + dt.timedelta(hours=1, minutes=5))
    assert sent["mails"] == []  # mail is not chosen for these types
    tg = [r for r in sent["http"] if "telegram" in str(r.url)]
    hook = [r for r in sent["http"] if "hooks.example" in str(r.url)]
    assert len(tg) == 1 and len(hook) == 1  # services go nowhere
    assert "/bot123:abc/sendMessage" in str(tg[0].url)
    body = json.loads(tg[0].content)
    assert body["chat_id"] == "42" and "Op blacklist Spamhaus ZEN" in body["text"]
    payload = json.loads(hook[0].content)
    assert payload["code"] == "blacklist" and payload["level"] == "critical" and payload["event"] == "firing"
    ts = hook[0].headers["X-Mailfilter-Timestamp"]
    assert hook[0].headers["X-Mailfilter-Signature"] == notify.sign("s3cret", ts, hook[0].content)


def test_one_broken_channel_does_not_stop_others(users, sent):
    save(webhook_enabled=True, webhook_url="https://fail.example/")
    run([notify.Alert("queue", "queue", "queue", "warning", {})], NOW)
    assert len(sent["mails"]) == 1
    with session() as db:
        assert notify.history(db)[0]["channels"] == {"email": "ok", "webhook": "webhook_status:500"}


def test_api_settings_test_and_chats(admin, customer, sent):
    assert customer.get("/api/settings/notifications").status_code == 403
    r = admin.get("/api/settings/notifications")
    assert r.status_code == 200 and r.json()["email_default"] == ["admin@rhis.nl"]
    form = {"telegram_enabled": True, "telegram_chat_id": "-100123", "telegram_token": "123:abc",
            "webhook_enabled": True, "webhook_url": "https://hooks.example/a", "webhook_format": "slack",
            "email_to": ["ops@rhis.nl"], "types": {"queue": {"channels": ["email"], "threshold": 50}}}
    r = admin.put("/api/settings/notifications", json=form)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["has_telegram_token"] and not d["has_webhook_secret"] and d["types"]["queue"] == {"channels": ["email"], "threshold": 50}
    # token kept when not sent again
    r = admin.put("/api/settings/notifications", json={**form, "telegram_token": None})
    assert r.json()["has_telegram_token"]

    for ch in ("email", "telegram", "webhook"):
        r = admin.post("/api/settings/notifications/test", json={**form, "telegram_token": None, "channel": ch})
        assert r.status_code == 200, (ch, r.text)
    assert sent["mails"][-1][0] == ["ops@rhis.nl"]
    assert json.loads(sent["http"][-1].content) == {"text": json.loads(sent["http"][-1].content)["text"]}

    r = admin.post("/api/settings/notifications/test", json={**form, "telegram_token": "999:bad", "channel": "telegram"})
    assert r.status_code == 400 and r.json()["detail"] == "telegram_bad_token"
    r = admin.post("/api/settings/notifications/telegram/chats", json={})
    assert [c["name"] for c in r.json()["chats"]] == ["Ops", "Reinder"]
    assert admin.put("/api/settings/notifications", json={"email_to": ["nope"]}).status_code == 400


def test_abuse_pause_goes_through_notifications(users, sent):
    types = {t: {"channels": ["telegram"]} for t in notify.TYPES}
    save(telegram_enabled=True, telegram_chat_id="42", email_enabled=False, types=types)
    with session() as db:
        notify.save_settings(db, notify.load_settings(db), telegram_token="123:abc")
        db.commit()
        a = notify.Alert("abuse", "abuse", "abuse:x", "critical", {"count": 1, "lines_en": "- x", "lines_nl": "- y"})
        assert notify.event(db, a) == {"telegram": "ok"}
    assert sent["mails"] == [] and "- x" in json.loads(sent["http"][-1].content)["text"]


def test_certificate_check(tmp_path, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "mailfilter.rhis.nl")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(NOW - dt.timedelta(days=80)).not_valid_after(NOW + dt.timedelta(days=10))
            .sign(key, hashes.SHA256()))
    f = tmp_path / "fullchain.pem"
    f.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setenv("MF_CERT_FILE", str(f))
    assert notify.check_certificate(5, NOW) == []
    [a] = notify.check_certificate(14, NOW)
    assert a.params["days"] == 10 and a.level == "warning"
    monkeypatch.setenv("MF_CERT_FILE", str(tmp_path / "missing.pem"))
    assert notify.check_certificate(14, NOW) == []


def test_backup_check(monkeypatch):
    from mailfilter import ctl

    data = {
        ("job", "list"): {"jobs": [
            {"id": "b", "kind": "backup", "state": "failed", "created": "2026-11-12T03:00:00Z", "message": "disk full"},
            {"id": "a", "kind": "backup", "state": "done", "created": "2026-11-11T03:00:00Z"}]},
        ("backup", "list"): {"config": {"enabled": True}, "last": {"created": "2026-11-09T03:00:00Z"}},
    }
    monkeypatch.setattr(ctl, "call", lambda *a, **k: data[a])
    found = {a.code: a for a in notify.check_backups(48, NOW)}
    assert found["backup_failed"].params["message"] == "disk full"
    assert found["backup_overdue"].params["hours"] == 48
    data[("job", "list")]["jobs"][0]["state"] = "running"
    data[("backup", "list")]["last"]["created"] = "2026-11-12T03:00:00Z"
    assert notify.check_backups(48, NOW) == []


def test_queue_and_blacklist_checks(monkeypatch, tmp_path):
    from mailfilter import queuectl
    from mailfilter_dns import blacklist

    monkeypatch.setattr(queuectl, "summary", lambda: {"active": 3, "deferred": 1200, "hold": 1})
    [a] = notify.check_queue(200)
    assert a.level == "critical" and a.params["deferred"] == 1200
    assert notify.check_queue(5000) == []
    monkeypatch.setattr(blacklist, "load", lambda: {"listed": [{"ip": "203.0.113.25", "zone": "bl.spamcop.net", "name": "SpamCop"}]})
    [b] = notify.check_blacklist()
    assert b.key == "blacklist:203.0.113.25:bl.spamcop.net"
    assert "SpamCop" in b.render("en")[0]


def test_settings_survive_round_trip(users):
    save(types={"queue": {"channels": ["webhook", "nope"], "threshold": 10}})
    with session() as db:
        raw = get_setting(db, notify.SETTINGS_KEY)
        s = notify.load_settings(db)
    assert raw["types"]["queue"]["channels"] == ["webhook"]
    assert s.types["queue"].threshold == 10 and s.types["certificate"].threshold == 14
