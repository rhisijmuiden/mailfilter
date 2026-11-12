"""DNS toolbox API: Cloudflare token, publishing, go-live and blocklist status,
against an in-memory Cloudflare and fixed DNS answers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "dns"))

from fakecf import FakeCloudflare  # noqa: E402

from mailfilter.api import dns as dns_api  # noqa: E402
from mailfilter_dns import blacklist, golive, publish  # noqa: E402
from mailfilter_dns.cloudflare import Cloudflare  # noqa: E402
from mailfilter_dns.config import ServerConfig  # noqa: E402
from mailfilter_dns.probe import CertInfo, SmtpProbe  # noqa: E402
from mailfilter_dns.resolver import StaticResolver  # noqa: E402

TOKEN = "good-token-0123456789abcdef"
ACCOUNT_TOKEN = "account-token-0123456789abcdef"
SERVER = ServerConfig("mailfilter.rhis.nl", ["203.0.113.10"], ["2a02:58:1f:10::25"], "_spf.mailfilter.rhis.nl")


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = FakeCloudflare(["rhis.nl"], [
        {"type": "TXT", "name": "rhis.nl", "content": "v=spf1 mx -all"},
        {"type": "MX", "name": "rhis.nl", "content": "pmg.rhis.nl", "priority": 10},
    ])
    fake.token = "good"
    resolver = StaticResolver({
        ("rhis.nl", "MX"): [(10, "pmg.rhis.nl")],
        ("rhis.nl", "TXT"): ["v=spf1 mx include:_spf.mailfilter.rhis.nl -all"],
        ("mailfilter.rhis.nl", "A"): ["203.0.113.10"],
    })
    for b in blacklist.BLOCKLISTS:
        resolver.records[(f"2.0.0.127.{b.zone}", "A")] = ["127.0.0.2"]

    def make_cf(token):
        cf = fake.client()
        cf._headers["Authorization"] = {TOKEN: "Bearer good", ACCOUNT_TOKEN: "Bearer acct"}.get(token, "Bearer wrong")
        return cf

    monkeypatch.setattr(dns_api, "make_cloudflare", make_cf)
    monkeypatch.setattr(dns_api, "get_resolver", lambda: resolver)
    monkeypatch.setattr(dns_api, "get_server", lambda: SERVER)
    monkeypatch.setattr(dns_api, "relay_domains", lambda: {"rhis.nl": "relay:[mail.rhis.nl]:25"})
    monkeypatch.setattr(golive, "relay_domains", lambda: {"rhis.nl": "relay:[mail.rhis.nl]:25"})
    monkeypatch.setattr(golive.probe_mod, "smtp", lambda h, port=25, **k: SmtpProbe(h, h, port, reachable=True))
    monkeypatch.setattr(golive.probe_mod, "https_cert", lambda h, **k: (CertInfo([h], "2027-02-01", 80, True), ""))
    monkeypatch.setattr(golive.dkim_mod, "local_key", lambda d, *a, **k: None)
    monkeypatch.setattr(dns_api.dkim, "local_key", lambda d, *a, **k: None)
    monkeypatch.setattr(publish, "JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(golive, "GOLIVE_DIR", tmp_path / "golive")
    monkeypatch.setattr(blacklist, "STATUS_FILE", tmp_path / "blacklist.json")
    return fake


def test_customer_cannot_use_toolbox(customer, env):
    assert customer.get("/api/dns/check/rhis.nl").status_code == 403
    assert customer.put("/api/dns/cloudflare", json={"token": TOKEN}).status_code == 403
    # but sees whether the server is listed
    assert customer.get("/api/dns/blacklist").json() == {"checked_at": None, "listed": False}


def test_token_is_verified_and_stored_encrypted(admin, env):
    assert admin.get("/api/dns/cloudflare").json()["configured"] is False
    assert admin.put("/api/dns/cloudflare", json={"token": "x" * 30}).json()["detail"].startswith("cloudflare_token_rejected:")
    assert admin.put("/api/dns/cloudflare", json={"token": "0123456789abcdef0123456789abcdef01234"}).json()["detail"] == "cloudflare_global_key"
    r = admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    assert r.status_code == 200 and r.json()["zones"] == ["rhis.nl"]
    assert admin.get("/api/dns/cloudflare").json()["status"] == "active"
    assert admin.delete("/api/dns/cloudflare").status_code == 204
    assert admin.post("/api/dns/publish/plan", json={"kind": "spf", "domain": "rhis.nl"}).json()["detail"] == "cloudflare_not_configured"


def test_account_owned_token_is_accepted(admin, env):
    r = admin.put("/api/dns/cloudflare", json={"token": ACCOUNT_TOKEN})
    assert r.status_code == 200, r.text
    assert admin.get("/api/dns/cloudflare").json()["status"] == "active"
    assert admin.post("/api/dns/publish/plan", json={"kind": "spf", "domain": "rhis.nl"}).status_code == 200


def test_publish_spf_merge(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    plan = admin.post("/api/dns/publish/plan", json={"kind": "spf", "domain": "rhis.nl"}).json()
    [ch] = plan["changes"]
    assert ch["before"]["content"] == "v=spf1 mx -all"
    assert ch["after"]["content"] == "v=spf1 mx include:_spf.mailfilter.rhis.nl -all"
    r = admin.post("/api/dns/publish/apply", json={"kind": "spf", "domain": "rhis.nl"})
    assert r.status_code == 200, r.text
    batch = r.json()["batch"]
    assert admin.get("/api/dns/journal").json()[0]["batch"] == batch
    assert admin.post(f"/api/dns/undo/{batch}").status_code == 200
    assert admin.post(f"/api/dns/undo/{batch}").json()["detail"] == "already_undone"


def test_publish_rejects_bad_input(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    assert admin.post("/api/dns/publish/plan", json={"kind": "dmarc", "domain": "rhis.nl", "value": "v=DMARC1; p=x"}).status_code == 400
    assert admin.post("/api/dns/publish/plan", json={"kind": "spf", "domain": "not a domain"}).json()["detail"] == "invalid_domain"
    assert admin.post("/api/dns/publish/plan", json={"kind": "spf", "domain": "example.org"}).json()["detail"].startswith("zone_not_found")
    assert admin.post("/api/dns/publish/plan", json={"kind": "dkim", "domain": "rhis.nl"}).json()["detail"] == "no_dkim_key"


def test_generators(admin, env):
    r = admin.post("/api/dns/spf/generate", json={"includes": ["m365"], "ip4": ["198.51.100.7"], "all": "-"}).json()
    assert r["value"] == "v=spf1 ip4:198.51.100.7 include:_spf.mailfilter.rhis.nl include:spf.protection.outlook.com -all"
    assert admin.post("/api/dns/spf/generate", json={"ip4": ["2001:db8::1"]}).status_code == 400
    r = admin.post("/api/dns/dmarc/generate", json={"policy": "reject", "rua": ["dmarc@rhis.nl"]}).json()
    assert r["value"] == "v=DMARC1; p=reject; rua=mailto:dmarc@rhis.nl" and r["status"] == "ok"


def test_check_and_mx(admin, env):
    r = admin.get("/api/dns/check/rhis.nl").json()
    assert r["managed"] is True and {i["code"] for i in r["items"]} >= {"mx", "spf", "dkim", "dmarc"}
    r = admin.get("/api/dns/mx/rhis.nl?probe=false").json()
    assert r["mx"][0]["host"] == "pmg.rhis.nl"


def test_golive_flow(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    st = admin.get("/api/dns/golive/rhis.nl").json()
    assert st["preflight"]["ok"] is True, st
    assert [c["action"] for c in st["plan"]["changes"]] == ["create", "delete"]
    assert admin.post("/api/dns/golive/rhis.nl", json={"confirm": "other.nl"}).json()["detail"] == "confirm_mismatch"
    r = admin.post("/api/dns/golive/rhis.nl", json={"confirm": "rhis.nl"})
    assert r.status_code == 200, r.text
    assert [(x["priority"], x["content"]) for x in env.find("rhis.nl", "MX")] == [(10, "mailfilter.rhis.nl")]
    assert admin.post("/api/dns/golive/rhis.nl", json={"confirm": "rhis.nl"}).json()["detail"] == "already_live"
    assert admin.post("/api/dns/golive/rhis.nl/rollback").json()["status"] == "rolled_back"
    assert [x["content"] for x in env.find("rhis.nl", "MX")] == ["pmg.rhis.nl"]


def test_golive_blocked_by_preflight(admin, env, monkeypatch):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    monkeypatch.setattr(golive.probe_mod, "smtp", lambda h, port=25, **k: SmtpProbe(h, h, port, error="timeout"))
    assert admin.post("/api/dns/golive/rhis.nl", json={"confirm": "rhis.nl"}).json()["detail"] == "preflight_failed"


def test_blacklist_check_now(admin, customer, env):
    r = admin.post("/api/dns/blacklist/check")
    assert r.status_code == 200 and r.json()["listed"] == []
    assert admin.get("/api/dns/blacklist").json()["checked_at"]


def test_cloudflare_class_is_real():
    assert dns_api.Cloudflare is Cloudflare
