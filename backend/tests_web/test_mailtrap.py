"""Mailtrap: mailboxes, access per customer, caught mail, checks and "send anyway"."""
import json
from pathlib import Path

import pytest

from mailfilter import gateway
from mailfilter.api import mailtrap as api
from mailfilter.mailtrap import deliver, store

from conftest import Client, PASSWORD

MAIL = b"""From: De Vries Webshop <noreply@webshop-devries.nl>
To: j.devries@gmail.com
Subject: Je bestelling {{ order.id }} is ontvangen
Date: Thu, 12 Nov 2026 10:41:07 +0100
Message-ID: <1@staging>
X-Spam-Status: No, score=1.20
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="b1"

--b1
Content-Type: text/plain; charset=utf-8

Bekijk: https://www.webshop-devries.nl/order/1

--b1
Content-Type: text/html; charset=utf-8

<p>Hoi</p><a href="http://staging.webshop-devries.local:8080/order/1">Bekijk</a><img src="logo.png">
--b1--
"""


@pytest.fixture(autouse=True)
def helper(monkeypatch, tmp_path):
    calls = []

    def fake(config_json):
        calls.append(json.loads(config_json))
        return {"ok": True, "changed": [], "warnings": []}

    monkeypatch.setattr(gateway, "run_helper", fake)
    monkeypatch.setattr(api, "mailtrap_root", lambda: tmp_path)
    return calls


def catch(box: dict, tmp: Path, raw: bytes = MAIL, rcpts=("j.devries@gmail.com",), sent=None) -> str:
    """What the mftrap pipe does on the server."""
    cfg = {"enabled": True, "boxes": {box["slug"]: {
        "mode": box["mode"], "allow": box["allow"], "retention_days": 7, "max_messages": 1000,
        "maildir": str(tmp / box["login"] / "Maildir")}}}
    return deliver.deliver(box["slug"], raw, client="10.0.8.21", sender="noreply@webshop-devries.nl",
                           recipients=list(rcpts), config=cfg,
                           sendmail=lambda raw, s, r: (sent if sent is not None else []).append(r))


BOX = {"name": "Webshop De Vries staging", "domain": "klant.nl",
       "networks": [{"cidr": "10.0.8.21", "description": "staging-web01"}, {"cidr": "10.0.8.0/30"}],
       "imap": True}


def test_module_is_off_until_switched_on(admin, helper):
    assert admin.get("/api/mailtrap").json()["enabled"] is False
    r = admin.put("/api/mailtrap/settings", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert helper[-1]["mailtrap_enabled"] is True
    assert admin.get("/api/mailtrap").json()["enabled"] is True


def test_create_box_goes_to_the_server(admin, helper):
    r = admin.post("/api/mailtrap/boxes", json=BOX)
    assert r.status_code == 201, r.text
    box = r.json()
    assert box["slug"] == "webshop-de-vries-staging" and box["login"] == "trap-webshop-de-vries-staging"
    assert len(box["password"]) >= 20
    sent = helper[-1]["mailtraps"][0]
    assert sent["networks"] == ["10.0.8.21", "10.0.8.0/30"] and sent["imap"] is True
    assert sent["password_hash"].startswith("{SSHA512}")


def test_network_also_in_relay_access_is_refused(admin, helper):
    assert admin.post("/api/gateway/relay-networks", json={"cidr": "10.0.8.21"}).status_code == 201
    r = admin.post("/api/mailtrap/boxes", json=BOX)
    assert r.status_code == 422
    codes = [p["code"] for p in r.json()["detail"]["problems"]]
    assert "mailtrap_network_is_relay" in codes
    assert admin.get("/api/mailtrap").json()["boxes"] == []  # nothing saved


def test_same_network_in_two_mailboxes_is_refused(admin):
    assert admin.post("/api/mailtrap/boxes", json=BOX).status_code == 201
    r = admin.post("/api/mailtrap/boxes", json={**BOX, "name": "Other"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "network_in_other_mailbox"


def test_customer_sees_own_mailbox_and_its_mail(admin, customer, tmp_path):
    box = admin.post("/api/mailtrap/boxes", json=BOX).json()
    other = admin.post("/api/mailtrap/boxes", json={"name": "Intern", "networks": [{"cidr": "10.9.0.1"}]}).json()
    catch(box, tmp_path)
    boxes = customer.get("/api/mailtrap").json()["boxes"]
    assert [b["id"] for b in boxes] == [box["id"]] and boxes[0]["unread"] == 1
    assert customer.get(f"/api/mailtrap/boxes/{other['id']}/messages").status_code == 404
    assert customer.post("/api/mailtrap/boxes", json=BOX).status_code == 403
    # Module off: customers do not see it in the menu
    assert customer.get("/api/mailtrap/unread").json()["visible"] is False
    assert admin.put("/api/mailtrap/settings", json={"enabled": True}).status_code == 200
    menu = customer.get("/api/mailtrap/unread").json()
    assert menu["unread"] == 1 and menu["visible"] is True

    msgs = customer.get(f"/api/mailtrap/boxes/{box['id']}/messages").json()["messages"]
    assert len(msgs) == 1
    m = msgs[0]
    assert m["subject"].startswith("Je bestelling") and m["to"] == ["j.devries@gmail.com"] and not m["seen"]
    assert m["score"] == 1.2
    assert {"code": "links_test", "status": "bad"} in m["issues"]
    assert {"code": "template", "status": "bad"} in m["issues"]

    d = customer.get(f"/api/mailtrap/boxes/{box['id']}/messages/{m['id']}").json()
    assert d["envelope_to"] == ["j.devries@gmail.com"] and d["client"] == "10.0.8.21"
    assert "Bekijk" in d["html"] and "order/1" in d["text"]
    codes = {c["code"]: c for c in d["checks"]}
    assert codes["links_test"]["params"]["links"] == ["http://staging.webshop-devries.local:8080/order/1"]
    assert codes["template"]["params"]["fields"] == ["{{ order.id }}"]
    assert codes["images_alt"]["status"] == "warn"
    assert codes["spam"]["status"] == "ok"
    assert d["checks"][0]["status"] == "bad"  # problems first
    # Opening marks it read (also for IMAP: moved to cur/ with the S flag)
    assert customer.get(f"/api/mailtrap/boxes/{box['id']}/messages").json()["messages"][0]["seen"] is True
    assert list((tmp_path / box["login"] / "Maildir" / "cur").iterdir())[0].name.endswith(":2,S")

    raw = customer.get(f"/api/mailtrap/boxes/{box['id']}/messages/{m['id']}/raw")
    assert raw.status_code == 200 and raw.content.startswith(b"X-Mailfilter-Trap:")
    # Customers cannot send it anyway
    assert customer.post(f"/api/mailtrap/boxes/{box['id']}/messages/{m['id']}/release").status_code == 403
    assert customer.delete(f"/api/mailtrap/boxes/{box['id']}/messages/{m['id']}").status_code == 204
    assert customer.get(f"/api/mailtrap/boxes/{box['id']}/messages").json()["total"] == 0


def test_user_given_access(admin, users, tmp_path):
    from mailfilter.db import session
    from mailfilter.models import User
    from mailfilter.security import hash_password

    with session() as db:
        dev = User(email="dev@bureau.nl", role="customer", password_hash=hash_password(PASSWORD))
        db.add(dev)
        db.commit()
        dev_id = dev.id
    box = admin.post("/api/mailtrap/boxes", json={"name": "CRM", "networks": [{"cidr": "10.1.1.1"}],
                                                  "users": [dev_id]}).json()
    assert box["user_emails"] == ["dev@bureau.nl"]
    c = Client().login("dev@bureau.nl")
    assert [b["name"] for b in c.get("/api/mailtrap").json()["boxes"]] == ["CRM"]


def test_send_anyway_and_except_mode(admin, tmp_path, monkeypatch):
    box = admin.post("/api/mailtrap/boxes", json={**BOX, "mode": "except", "allow": ["@RHIS.nl"]}).json()
    assert box["allow"] == ["@rhis.nl"]
    sent_on = []
    catch(box, tmp_path, rcpts=("j.devries@gmail.com", "tester@rhis.nl"), sent=sent_on)
    assert sent_on == [["tester@rhis.nl"]]  # testers get the real mail
    m = admin.get(f"/api/mailtrap/boxes/{box['id']}/messages").json()["messages"][0]
    d = admin.get(f"/api/mailtrap/boxes/{box['id']}/messages/{m['id']}").json()
    assert d["delivered_to"] == ["tester@rhis.nl"]

    relayed = []
    monkeypatch.setattr(api, "relay", lambda raw, s, r: relayed.append((raw, s, r)))
    r = admin.post(f"/api/mailtrap/boxes/{box['id']}/messages/{m['id']}/release")
    assert r.status_code == 200, r.text
    raw, sender, rcpts = relayed[0]
    assert rcpts == ["j.devries@gmail.com"] and sender == "noreply@webshop-devries.nl"
    assert b"X-Mailfilter" not in raw and raw.startswith(b"From: De Vries")
    log = admin.get("/api/audit?action=mailtrap_released").json()
    assert "mailtrap_released" in json.dumps(log)


def test_delete_box_removes_mail(admin, tmp_path):
    box = admin.post("/api/mailtrap/boxes", json=BOX).json()
    catch(box, tmp_path)
    assert admin.delete(f"/api/mailtrap/boxes/{box['id']}").status_code == 204
    assert not (tmp_path / box["login"]).exists()


def test_retention(tmp_path):
    md = tmp_path / "Maildir"
    for _ in range(5):
        store.write_message(md, MAIL)
    res = store.cleanup(md, retention_days=7, max_messages=3)
    assert res.removed == 2 and len(store.entries(md)) == 3
    res = store.cleanup(md, retention_days=1, max_messages=3, now=__import__("time").time() + 3 * 86400)
    assert res.removed == 3 and store.entries(md) == []
