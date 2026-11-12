"""DNS toolbox API: Cloudflare token, publishing, go-live and blocklist status,
against an in-memory Cloudflare and fixed DNS answers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "dns"))

from fakecf import FakeCloudflare  # noqa: E402

from mailfilter.api import dns as dns_api  # noqa: E402
from mailfilter_dns import blacklist, golive, publish, tlssec  # noqa: E402
from mailfilter_dns.cloudflare import Cloudflare  # noqa: E402
from mailfilter_dns.config import ServerConfig  # noqa: E402
from mailfilter_dns.probe import CertInfo, SmtpProbe  # noqa: E402
from mailfilter_dns.resolver import StaticResolver  # noqa: E402

TOKEN = "good-token-0123456789abcdef"
ACCOUNT_TOKEN = "account-token-0123456789abcdef"
TLSA = "3 1 1 " + "ab" * 32
env_resolver: list = [None]
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
    monkeypatch.setattr(tlssec, "STS_FILE", tmp_path / "mta-sts.json")
    monkeypatch.setattr(dns_api, "local_tlsa", lambda h: TLSA)
    monkeypatch.setattr(dns_api, "fetch_sts_policy", lambda d: (_ for _ in ()).throw(OSError("no network in tests")))
    env_resolver[0] = resolver
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


def test_published_record_shows_as_waiting_for_dns(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    value = "v=DMARC1; p=quarantine; rua=mailto:dmarc@rhis.nl"
    assert admin.post("/api/dns/publish/apply", json={"kind": "dmarc", "domain": "rhis.nl", "value": value}).status_code == 200
    # DNS (the resolver) does not have it yet; Cloudflare does
    item = {i["code"]: i for i in admin.get("/api/dns/check/rhis.nl").json()["items"]}["dmarc"]
    assert item["status"] == "pending" and item["value"] == value and item["problems"] == ["waiting_for_dns"]
    # Publishing again is a no-op, not an error
    plan = admin.post("/api/dns/publish/plan", json={"kind": "dmarc", "domain": "rhis.nl", "value": value}).json()
    assert [c["action"] for c in plan["changes"]] == ["none"]


def test_golive_status_error_shows_reason(admin, env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("probe exploded: badly")
    monkeypatch.setattr(golive, "preflight", boom)
    r = admin.get("/api/dns/golive/rhis.nl")
    assert r.status_code == 500 and r.json()["detail"].startswith("golive_error:RuntimeError probe exploded badly")


def test_mx_editor(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    cur = admin.get("/api/dns/mx/rhis.nl/cloudflare").json()
    assert cur["records"] == [{"priority": 10, "host": "pmg.rhis.nl"}] and cur["managed"] is True
    body = {"kind": "mx", "domain": "rhis.nl", "mx": [{"priority": 10, "host": "mailfilter.rhis.nl"},
                                                     {"priority": 20, "host": "PMG.rhis.nl."}]}
    plan = admin.post("/api/dns/publish/plan", json=body).json()
    assert [(c["action"], (c["after"] or {}).get("priority")) for c in plan["changes"]] == [("create", 10), ("update", 20)]
    assert plan["warnings"] == []
    r = admin.post("/api/dns/publish/apply", json=body)
    assert r.status_code == 200, r.text
    assert sorted((x["priority"], x["content"]) for x in env.find("rhis.nl", "MX")) == [(10, "mailfilter.rhis.nl"), (20, "pmg.rhis.nl")]
    # Same list again: nothing to do
    assert {c["action"] for c in admin.post("/api/dns/publish/plan", json=body).json()["changes"]} == {"none"}
    # Undo puts the old MX back
    assert admin.post(f"/api/dns/undo/{r.json()['batch']}").status_code == 200
    assert [(x["priority"], x["content"]) for x in env.find("rhis.nl", "MX")] == [(10, "pmg.rhis.nl")]


def test_mx_editor_rejects_bad_input(admin, env, monkeypatch):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    plan = lambda mx: admin.post("/api/dns/publish/plan", json={"kind": "mx", "domain": "rhis.nl", "mx": mx})
    assert plan([]).json()["detail"] == "no_mx"
    assert plan([{"priority": 10, "host": "not a host"}]).json()["detail"].startswith("invalid_mx_host")
    assert plan([{"priority": 10, "host": "a.rhis.nl"}, {"priority": 20, "host": "a.rhis.nl"}]).json()["detail"] == "duplicate_mx"
    assert plan([{"priority": 70000, "host": "a.rhis.nl"}]).status_code == 422
    monkeypatch.setattr(dns_api, "relay_domains", lambda: {})
    assert plan([{"priority": 10, "host": "mailfilter.rhis.nl"}]).json()["warnings"] == ["not_relay_domain"]


def test_mta_sts_policy_hosting_and_publish(admin, client, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    st = admin.get("/api/dns/tls/rhis.nl").json()
    assert st["policy"] is None and st["policy_default"]["mx"] == ["mailfilter.rhis.nl", "pmg.rhis.nl"]
    # Publishing needs a saved policy first
    assert admin.post("/api/dns/publish/plan", json={"kind": "mta_sts", "domain": "rhis.nl"}).json()["detail"] == "no_mta_sts_policy"
    assert admin.put("/api/dns/mta-sts/example.org", json={"mx": ["mailfilter.rhis.nl"]}).json()["detail"] == "not_relay_domain"
    assert admin.put("/api/dns/mta-sts/rhis.nl", json={"mx": ["bad host"]}).json()["detail"].startswith("invalid_mx_pattern")
    pol = admin.put("/api/dns/mta-sts/rhis.nl", json={"mode": "testing", "mx": ["mailfilter.rhis.nl", "*.rhis.nl"]}).json()
    # Served without login, only on mta-sts.<relay domain>
    r = client.get("/api/mta-sts/policy", headers={"Host": "mta-sts.rhis.nl"})
    assert r.status_code == 200 and r.text == "version: STSv1\r\nmode: testing\r\nmx: mailfilter.rhis.nl\r\nmx: *.rhis.nl\r\nmax_age: 604800\r\n"
    assert client.get("/api/mta-sts/policy", headers={"Host": "mta-sts.example.org"}).status_code == 404
    assert client.get("/api/mta-sts/allowed?domain=mta-sts.rhis.nl").status_code == 200
    assert client.get("/api/mta-sts/allowed?domain=evil.example.org").status_code == 404
    # DNS: CNAME for the policy host first, then the TXT with the id
    body = {"kind": "mta_sts", "domain": "rhis.nl"}
    plan = admin.post("/api/dns/publish/plan", json=body).json()
    assert [(c["action"], c["type"]) for c in plan["changes"]] == [("create", "CNAME"), ("create", "TXT")]
    assert admin.post("/api/dns/publish/apply", json=body).status_code == 200
    [cname] = env.find("mta-sts.rhis.nl", "CNAME")
    assert cname["content"] == "mailfilter.rhis.nl" and cname["proxied"] is False
    assert env.find("_mta-sts.rhis.nl", "TXT")[0]["content"] == f'"v=STSv1; id={pol["id"]}"'
    # Same policy: same id, nothing to publish. New mode: new id, TXT updated
    assert admin.put("/api/dns/mta-sts/rhis.nl", json={"mode": "testing", "mx": ["mailfilter.rhis.nl", "*.rhis.nl"]}).json()["id"] == pol["id"]
    assert {c["action"] for c in admin.post("/api/dns/publish/plan", json=body).json()["changes"]} == {"none"}
    pol2 = admin.put("/api/dns/mta-sts/rhis.nl", json={"mode": "enforce", "mx": ["mailfilter.rhis.nl"]}).json()
    assert pol2["id"] > pol["id"]
    assert [c["action"] for c in admin.post("/api/dns/publish/plan", json=body).json()["changes"]] == ["none", "update"]


def test_mta_sts_check_reads_policy(admin, env, monkeypatch):
    r = env_resolver[0]
    r.records[("_mta-sts.rhis.nl", "TXT")] = ["v=STSv1; id=20261112000000"]
    monkeypatch.setattr(dns_api, "fetch_sts_policy", lambda d: "version: STSv1\nmode: testing\nmx: mailfilter.rhis.nl\nmax_age: 86400\n")
    item = {i["code"]: i for i in admin.get("/api/dns/check/rhis.nl").json()["items"]}["mta_sts"]
    assert item["status"] == "weak" and item["problems"] == ["mode_testing", "mx_not_in_policy:pmg.rhis.nl"]
    monkeypatch.setattr(dns_api, "fetch_sts_policy", lambda d: (_ for _ in ()).throw(OSError("refused")))
    item = {i["code"]: i for i in admin.get("/api/dns/check/rhis.nl").json()["items"]}["mta_sts"]
    assert item["status"] == "bad" and item["problems"][0].startswith("policy_unreachable:OSError")


def test_tls_rpt_publish(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    assert admin.get("/api/dns/tls/rhis.nl").json()["tlsrpt_default"] == "tlsrpt@rhis.nl"
    bad = admin.post("/api/dns/publish/plan", json={"kind": "tls_rpt", "domain": "rhis.nl", "value": "not-an-address"})
    assert bad.status_code == 400 and bad.json()["detail"].startswith("invalid_email")
    body = {"kind": "tls_rpt", "domain": "rhis.nl", "value": "tls@rhis.nl"}
    [ch] = admin.post("/api/dns/publish/plan", json=body).json()["changes"]
    assert ch["action"] == "create" and ch["after"]["content"] == "v=TLSRPTv1; rua=mailto:tls@rhis.nl"
    assert admin.post("/api/dns/publish/apply", json=body).status_code == 200
    assert env.find("_smtp._tls.rhis.nl", "TXT")


def test_dane_tlsa_publish_and_dnssec_warning(admin, env):
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    item = {i["code"]: i for i in admin.get("/api/dns/check/rhis.nl").json()["items"]}["dane"]
    assert item["status"] == "missing" and item["fix"] == TLSA
    assert "dnssec_off:mailfilter.rhis.nl" in item["problems"] and "dnssec_off:rhis.nl" in item["problems"]
    env.add({"type": "TLSA", "name": "_25._tcp.mailfilter.rhis.nl", "data": {"usage": 3, "selector": 1, "matching_type": 1, "certificate": "00" * 32}})
    body = {"kind": "tlsa"}
    plan = admin.post("/api/dns/publish/plan", json=body).json()
    assert plan["warnings"] == ["dnssec_off_host"]
    assert [c["action"] for c in plan["changes"]] == ["create", "delete"]
    assert admin.post("/api/dns/publish/apply", json=body).status_code == 200
    assert [x["content"] for x in env.find("_25._tcp.mailfilter.rhis.nl", "TLSA")] == [TLSA]
    # Signed zones and a published record that matches: DANE is ok
    r = env_resolver[0]
    r.records[("_25._tcp.mailfilter.rhis.nl", "TLSA")] = [TLSA]
    r.records[("mailfilter.rhis.nl", "SECURE")] = True
    r.records[("rhis.nl", "SECURE")] = True
    assert admin.post("/api/dns/publish/plan", json=body).json()["warnings"] == []
    item = {i["code"]: i for i in admin.get("/api/dns/check/rhis.nl").json()["items"]}["dane"]
    assert item["status"] == "ok" and item["problems"] == []


# --- BIMI ---------------------------------------------------------------------------------

BIMI_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" version="1.2" baseProfile="tiny-ps" viewBox="0 0 10 10">'
            '<title>RHIS</title><rect width="10" height="10" fill="#123"/></svg>')


@pytest.fixture
def bimi_env(env, tmp_path, monkeypatch):
    from mailfilter_dns import bimi

    monkeypatch.setattr(bimi, "BIMI_DIR", tmp_path / "bimi")
    env_resolver[0].records[("_dmarc.rhis.nl", "TXT")] = ["v=DMARC1; p=none"]
    return env


def test_bimi_logo_record_and_public_files(admin, customer, client, bimi_env):
    assert customer.get("/api/bimi/status/rhis.nl").status_code == 403
    assert admin.get("/api/bimi/status/example.org").json()["detail"] == "unknown_domain"
    st = admin.get("/api/bimi/status/rhis.nl").json()
    assert st["logo"] is None and st["dmarc"]["problems"] == ["dmarc_not_enforced:none"]

    # An ordinary logo is refused, the converted version is accepted
    plain = BIMI_SVG.replace(' version="1.2" baseProfile="tiny-ps"', "").replace("<title>RHIS</title>", "")
    r = admin.put("/api/bimi/logo/rhis.nl", json={"svg": plain})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "svg_invalid"
    chk = admin.post("/api/bimi/logo/rhis.nl/check", json={"svg": plain, "convert": True, "title": "RHIS"}).json()
    assert chk["report"]["ok"] and "set_profile" in chk["changes"] and chk["svg"].startswith("<?xml")
    st = admin.put("/api/bimi/logo/rhis.nl", json={"svg": plain, "convert": True, "title": "RHIS"}).json()
    assert st["logo"]["report"]["ok"] and st["expected"] == "v=BIMI1; l=https://mailfilter.rhis.nl/api/bimi/rhis.nl.svg;"
    assert admin.get("/api/bimi/domains").json()[0] == {"domain": "klant.nl", "logo": False, "cert": False}

    # Public: no login, served as an image
    r = client.get("/api/bimi/rhis.nl.svg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"tiny-ps" in r.content
    assert client.get("/api/bimi/rhis.nl.pem").status_code == 404
    assert client.get("/api/bimi/..%2Fetc.svg").status_code == 404

    # Publish through the DNS toolbox, with a warning while DMARC is not enforced
    admin.put("/api/dns/cloudflare", json={"token": TOKEN})
    plan = admin.post("/api/dns/publish/plan", json={"kind": "bimi", "domain": "rhis.nl"}).json()
    assert plan["warnings"] == ["bimi_dmarc_not_enforced"]
    assert plan["changes"][0]["after"]["content"] == st["expected"] and plan["changes"][0]["name"] == "default._bimi.rhis.nl"
    assert admin.post("/api/dns/publish/apply", json={"kind": "bimi", "domain": "rhis.nl"}).status_code == 200
    assert bimi_env.find("default._bimi.rhis.nl", "TXT")[0]["content"].strip('"') == st["expected"]

    assert admin.delete("/api/bimi/logo/rhis.nl").json()["logo"] is None
    assert admin.post("/api/dns/publish/plan", json={"kind": "bimi", "domain": "rhis.nl"}).json()["detail"] == "no_bimi_logo"
    assert admin.put("/api/bimi/cert/rhis.nl", json={"pem": "nope"}).json()["detail"] == {"code": "cert_invalid", "problems": ["not_pem"]}
