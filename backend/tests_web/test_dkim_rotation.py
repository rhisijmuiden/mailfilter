"""Automatic DKIM key rotation, against a fake key helper, an in-memory
Cloudflare and DNS answers read from that Cloudflare."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "dns"))

from fakecf import FakeCloudflare  # noqa: E402

from mailfilter import dkim_rotation as rot  # noqa: E402
from mailfilter.db import session  # noqa: E402
from mailfilter.models import AuditLog  # noqa: E402
from mailfilter_dns import publish  # noqa: E402
from mailfilter_dns.cloudflare import txt_value  # noqa: E402

T0 = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)


def snippet(sel: str, p: str) -> str:
    return f'{sel}._domainkey IN TXT ( "v=DKIM1; k=rsa; " "p={p}" ) ;\n'


class FakeHelper:
    """The root helper's key store: domain -> active selector and keys."""

    def __init__(self):
        self.domains: dict[str, dict] = {}
        self.calls: list[str] = []

    def add(self, domain, sel, created, p):
        self.domains.setdefault(domain, {"active": sel, "keys": {}})["keys"][sel] = (created, snippet(sel, p))

    def __call__(self, req):
        a = req["action"]
        self.calls.append(a)
        if a == "status":
            return {"ok": True, "domains": [
                {"domain": d, "active": v["active"],
                 "keys": [{"selector": s, "created": c.isoformat(), "txt": t} for s, (c, t) in v["keys"].items()]}
                for d, v in self.domains.items()]}
        d, s = req["domain"], req["selector"]
        v = self.domains[d]
        if a == "generate":
            v["keys"].setdefault(s, (T0, snippet(s, "NEW" + s.upper())))
            return {"ok": True, "txt": v["keys"][s][1]}
        if a == "select":
            changed = v["active"] != s
            v["active"] = s
            return {"ok": True, "changed": changed}
        if a == "retire":
            return {"ok": True, "removed": v["keys"].pop(s, None) is not None}
        raise AssertionError(a)


class CfResolver:
    """TXT answers straight from the fake Cloudflare, plus fixed ones."""

    def __init__(self, fake, extra=None):
        self.fake, self.extra = fake, extra or {}

    def txt(self, name):
        return [txt_value(r["content"]) for r in self.fake.records.values() if r["name"] == name and r["type"] == "TXT"] \
            + self.extra.get(name, [])


@pytest.fixture
def env(monkeypatch, tmp_path, users):
    fake = FakeCloudflare(["rhis.nl"], [
        {"type": "TXT", "name": "mf2026._domainkey.rhis.nl", "content": "v=DKIM1; k=rsa; p=OLDRHIS"},
    ])
    fake.token = "good"
    h = FakeHelper()
    old = T0 - timedelta(days=200)
    h.add("rhis.nl", "mf2026", old, "OLDRHIS")
    h.add("klant.nl", "mf2026", old, "OLDKLANT")
    res = CfResolver(fake, {"mf2026._domainkey.klant.nl": ["v=DKIM1; k=rsa; p=OLDKLANT"]})
    monkeypatch.setattr(rot, "helper", h)
    monkeypatch.setattr(rot, "cloudflare", lambda db: fake.client())
    monkeypatch.setattr(rot, "resolver_for", lambda d: res)
    monkeypatch.setattr(publish, "JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setenv("MF_CLUSTER_PUSH", "0")
    return fake, h, res


def run(at):
    with session() as db:
        out = rot.run(db, at=at)
        db.commit()
        return out


def test_full_rotation_through_cloudflare(env):
    fake, h, _ = env
    out = run(T0)
    assert out.ran and not out.errors, out
    # Only the Cloudflare domain starts; the new record is there and already seen
    assert any("rhis.nl: started, new selector mf202611" == e for e in out.events)
    assert txt_value(fake.find("mf202611._domainkey.rhis.nl", "TXT")[0]["content"]) == "v=DKIM1; k=rsa; p=NEWMF202611"
    with session() as db:
        st = rot._state(db)["domains"]
    assert set(st) == {"rhis.nl"} and st["rhis.nl"]["phase"] == "wait"
    assert h.domains["rhis.nl"]["active"] == "mf2026"

    run(T0 + timedelta(hours=10))
    assert h.domains["rhis.nl"]["active"] == "mf2026"  # still waiting for caches
    run(T0 + timedelta(hours=49))
    assert h.domains["rhis.nl"]["active"] == "mf202611"
    # Both records stay in DNS during the overlap
    assert fake.find("mf2026._domainkey.rhis.nl", "TXT") and "mf2026" in h.domains["rhis.nl"]["keys"]

    run(T0 + timedelta(days=5))
    assert fake.find("mf2026._domainkey.rhis.nl", "TXT")
    out = run(T0 + timedelta(hours=49) + timedelta(days=7, minutes=1))
    assert "rhis.nl: finished, mf2026 -> mf202611" in out.events
    assert not fake.find("mf2026._domainkey.rhis.nl", "TXT")
    assert fake.find("mf202611._domainkey.rhis.nl", "TXT")
    assert "mf2026" not in h.domains["rhis.nl"]["keys"]
    with session() as db:
        st = rot._state(db)
        assert st["domains"] == {}
        assert st["history"][-1]["old_record"] == "removed"
        actions = [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]
    assert actions == ["dkim_rotation_started", "dkim_rotation_published", "dkim_rotation_switched",
                       "dkim_rotation_finished"]
    # The next rotation is half a year away
    assert run(T0 + timedelta(days=30)).events == []


def test_not_due_and_not_live_domains_wait(env):
    fake, h, res = env
    h.domains["rhis.nl"]["keys"]["mf2026"] = (T0 - timedelta(days=30), h.domains["rhis.nl"]["keys"]["mf2026"][1])
    assert run(T0).events == []
    # Manual DNS on: klant.nl starts too, and waits for its record to be published by hand
    with session() as db:
        rot.set_setting(db, rot.CONFIG_KEY, {**rot.DEFAULTS, "manual_dns": True})
        db.commit()
    run(T0)
    with session() as db:
        e = rot._state(db)["domains"]["klant.nl"]
    assert e["phase"] == "publish" and e["auto"] is False and e["dns"] == "missing"
    res.extra["mf202611._domainkey.klant.nl"] = ["v=DKIM1; k=rsa; p=NEWMF202611"]
    run(T0 + timedelta(hours=1))
    with session() as db:
        assert rot._state(db)["domains"]["klant.nl"]["phase"] == "wait"


def test_switched_off(env):
    with session() as db:
        rot.set_setting(db, rot.CONFIG_KEY, {**rot.DEFAULTS, "enabled": False})
        db.commit()
    assert run(T0).events == []


def test_api_rotate_now_and_cancel(admin, customer, env):
    fake, h, _ = env
    assert customer.get("/api/dkim").status_code == 403
    r = admin.get("/api/dkim").json()
    rows = {d["domain"]: d for d in r["domains"]}
    assert rows["rhis.nl"]["cloudflare"] is True and rows["klant.nl"]["cloudflare"] is False
    assert rows["rhis.nl"]["selector"] == "mf2026" and rows["rhis.nl"]["record"] == "v=DKIM1; k=rsa; p=OLDRHIS"

    r = admin.post("/api/dkim/klant.nl/rotate")
    assert r.status_code == 200, r.text
    row = {d["domain"]: d for d in r.json()["domains"]}["klant.nl"]
    assert row["rotation"]["phase"] == "publish"
    assert row["rotation"]["new_name"].endswith("._domainkey.klant.nl")
    assert row["rotation"]["new_record"].startswith("v=DKIM1; k=rsa; p=NEW")
    assert admin.post("/api/dkim/klant.nl/rotate").json()["detail"] == "already_rotating"
    assert admin.post("/api/dkim/nope.nl/rotate").status_code == 404

    r = admin.post("/api/dkim/klant.nl/cancel")
    assert r.status_code == 200
    assert {d["domain"]: d for d in r.json()["domains"]}["klant.nl"]["rotation"] is None
    assert len(h.domains["klant.nl"]["keys"]) == 1

    r = admin.put("/api/dkim/settings", json={"enabled": True, "interval_months": 12, "overlap_days": 10,
                                              "publish_wait_hours": 24, "manual_dns": False})
    assert r.json()["interval_months"] == 12
    assert admin.put("/api/dkim/settings", json={"enabled": True, "interval_months": 0, "overlap_days": 10,
                                                 "publish_wait_hours": 24, "manual_dns": False}).status_code == 422
    assert admin.post("/api/dkim/run").json()["ran"] is True
