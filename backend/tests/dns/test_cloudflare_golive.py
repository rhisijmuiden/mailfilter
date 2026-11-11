import pytest
from fakecf import FakeCloudflare

from mailfilter_dns import blacklist, golive, publish
from mailfilter_dns.cloudflare import CloudflareError, txt_quote, txt_value
from mailfilter_dns.config import ServerConfig, destination_host, relay_domains
from mailfilter_dns.dkim import DkimKey
from mailfilter_dns.domaincheck import check_domain
from mailfilter_dns.lookup import mx_lookup
from mailfilter_dns.probe import CertInfo, SmtpProbe, name_matches
from mailfilter_dns.resolver import StaticResolver

SERVER = ServerConfig("mailfilter.rhis.nl", ["203.0.113.10"], ["2a02:58:1f:10::25"], "_spf.mailfilter.rhis.nl")


def test_txt_quoting():
    long = "v=DKIM1; k=rsa; p=" + "A" * 400
    q = txt_quote(long)
    assert q.count('"') == 4 and txt_value(q) == long
    assert txt_value("v=spf1 -all") == "v=spf1 -all"


def test_bad_token():
    fake = FakeCloudflare(["rhis.nl"], token="wrong")
    with pytest.raises(CloudflareError) as e:
        fake.client().verify_token()
    assert e.value.status == 403 and 9109 in e.value.codes


def test_zone_for_subdomain():
    cf = FakeCloudflare(["rhis.nl"]).client()
    assert cf.zone_for("mf2026._domainkey.rhis.nl").name == "rhis.nl"
    assert cf.zone_for("example.org") is None


def test_publish_spf_merge_apply_and_undo(tmp_path):
    fake = FakeCloudflare(["rhis.nl"], [
        {"type": "TXT", "name": "rhis.nl", "content": '"v=spf1 mx include:spf.transip.email -all"'},
        {"type": "TXT", "name": "rhis.nl", "content": "google-site-verification=abc"},
    ])
    cf = fake.client()
    zone = cf.zone_for("rhis.nl")
    ch = publish.plan_spf(cf, zone, "rhis.nl", [SERVER.spf_include])
    assert ch.action == "update"
    assert ch.before["content"] == "v=spf1 mx include:spf.transip.email -all"
    assert ch.after["content"] == "v=spf1 mx include:spf.transip.email include:_spf.mailfilter.rhis.nl -all"

    j = tmp_path / "journal.jsonl"
    plan = publish.Plan(zone.id, zone.name, "rhis.nl", [ch])
    done = publish.apply(cf, plan, actor="reinder", journal=j)
    [rec] = [r for r in fake.find("rhis.nl", "TXT") if "spf1" in r["content"]]
    assert txt_value(rec["content"]).endswith("include:_spf.mailfilter.rhis.nl -all")
    assert rec["comment"] == "Managed by Mailfilter"
    # Second plan is a no-op
    assert publish.plan_spf(cf, zone, "rhis.nl", [SERVER.spf_include]).noop

    publish.undo_batch(cf, done[0]["batch"], journal=j)
    [rec] = [r for r in fake.find("rhis.nl", "TXT") if "spf1" in r["content"]]
    assert txt_value(rec["content"]) == "v=spf1 mx include:spf.transip.email -all"
    with pytest.raises(ValueError, match="already_undone"):
        publish.undo_batch(cf, done[0]["batch"], journal=j)


def test_publish_refuses_two_spf_records():
    fake = FakeCloudflare(["rhis.nl"], [
        {"type": "TXT", "name": "rhis.nl", "content": "v=spf1 -all"},
        {"type": "TXT", "name": "rhis.nl", "content": "v=spf1 mx -all"},
    ])
    cf = fake.client()
    with pytest.raises(ValueError, match="multiple_spf"):
        publish.plan_spf(cf, cf.zone_for("rhis.nl"), "rhis.nl", [SERVER.spf_include])


def test_publish_dkim_dmarc_and_server_spf(tmp_path):
    fake = FakeCloudflare(["rhis.nl"])
    cf = fake.client()
    zone = cf.zone_for("rhis.nl")
    key = DkimKey("rhis.nl", "mf2026", "PUBKEY")
    plan = publish.Plan(zone.id, zone.name, "rhis.nl", [
        publish.plan_dkim(cf, zone, key),
        publish.plan_dmarc(cf, zone, "rhis.nl", "v=DMARC1; p=quarantine; rua=mailto:dmarc@rhis.nl"),
        publish.plan_server_spf(cf, zone, SERVER),
    ])
    assert [c.action for c in plan.changes] == ["create"] * 3
    publish.apply(cf, plan, journal=tmp_path / "j")
    assert txt_value(fake.find("mf2026._domainkey.rhis.nl", "TXT")[0]["content"]) == "v=DKIM1; k=rsa; p=PUBKEY"
    assert txt_value(fake.find("_spf.mailfilter.rhis.nl", "TXT")[0]["content"]) == \
        "v=spf1 ip4:203.0.113.10 ip6:2a02:58:1f:10::25 -all"
    assert publish.plan_dkim(cf, zone, key).noop
    with pytest.raises(Exception):
        publish.plan_dmarc(cf, zone, "rhis.nl", "v=DMARC1; p=whatever")


def _preflight(**kw):
    resolver = StaticResolver({
        ("rhis.nl", "TXT"): ["v=spf1 mx include:_spf.mailfilter.rhis.nl -all"],
        ("mf2026._domainkey.rhis.nl", "TXT"): ["v=DKIM1; k=rsa; p=PUB"],
        ("mailfilter.rhis.nl", "A"): ["203.0.113.10"],
        ("mailfilter.rhis.nl", "AAAA"): ["2a02:58:1f:10::25"],
    })
    args = dict(
        domains={"rhis.nl": "relay:[mail.rhis.nl]:25"},
        smtp=lambda h, p: SmtpProbe(h, h, p, reachable=True),
        cert=lambda h: (CertInfo(["mailfilter.rhis.nl"], "2027-02-01", 80, True), ""),
        dkim_key=DkimKey("rhis.nl", "mf2026", "PUB"),
    )
    args.update(kw)
    return golive.preflight("rhis.nl", SERVER, resolver, **args)


def test_preflight_all_green():
    pf = _preflight()
    assert pf.ok, pf.to_dict()
    assert all(c.ok for c in pf.checks)


def test_preflight_blocks_on_unknown_domain_and_dead_destination():
    pf = _preflight(domains={}, smtp=lambda h, p: SmtpProbe(h, h, p, error="timeout"))
    bad = {c.code for c in pf.checks if not c.ok}
    assert not pf.ok and {"accepts", "destination"} <= bad
    pf = _preflight(cert=lambda h: (None, "refused"))
    assert not pf.ok
    # SPF and DKIM are warnings only
    pf = _preflight(dkim_key=None)
    assert pf.ok and not next(c for c in pf.checks if c.code == "dkim").ok


def test_golive_plan_apply_rollback(tmp_path):
    fake = FakeCloudflare(["rhis.nl"], [
        {"type": "MX", "name": "rhis.nl", "content": "pmg.rhis.nl", "priority": 10},
        {"type": "MX", "name": "rhis.nl", "content": "pmg2.rhis.nl", "priority": 20},
    ])
    cf = fake.client()
    base, j = tmp_path / "golive", tmp_path / "j"
    plan = golive.plan(cf, "rhis.nl", SERVER, base=base)
    assert [c.action for c in plan.changes] == ["create", "delete", "delete"]

    st = golive.go_live(cf, plan, actor="reinder", base=base, journal=j)
    assert st["status"] == "live" and len(st["previous_mx"]) == 2
    mx = fake.find("rhis.nl", "MX")
    assert [(r["priority"], r["content"]) for r in mx] == [(10, "mailfilter.rhis.nl")]

    with pytest.raises(ValueError, match="already_live"):
        golive.plan(cf, "rhis.nl", SERVER, base=base)

    golive.rollback(cf, "rhis.nl", actor="reinder", base=base, journal=j)
    assert sorted((r["priority"], r["content"]) for r in fake.find("rhis.nl", "MX")) == \
        [(10, "pmg.rhis.nl"), (20, "pmg2.rhis.nl")]
    assert golive.state("rhis.nl", base)["status"] == "rolled_back"
    # After a rollback the domain can go live again
    assert golive.plan(cf, "rhis.nl", SERVER, base=base).needed


def test_golive_already_pointing_is_noop(tmp_path):
    fake = FakeCloudflare(["rhis.nl"], [{"type": "MX", "name": "rhis.nl", "content": "mailfilter.rhis.nl", "priority": 10}])
    plan = golive.plan(fake.client(), "rhis.nl", SERVER, base=tmp_path)
    assert plan.needed == []


# --- blocklists -------------------------------------------------------------

def _bl_resolver(listed: dict[str, list[str]]):
    recs = {}
    for b in blacklist.BLOCKLISTS:
        recs[(f"2.0.0.127.{b.zone}", "A")] = ["127.0.0.2"]
    for name, answers in listed.items():
        recs[(name, "A")] = answers
    return StaticResolver(recs)


def test_blacklist_listed_clean_unusable(tmp_path):
    r = _bl_resolver({
        "10.113.0.203.b.barracudacentral.org": ["127.0.0.2"],
        "10.113.0.203.zen.spamhaus.org": ["127.255.255.254"],  # public resolver refused
    })
    r.records[("10.113.0.203.b.barracudacentral.org", "TXT")] = ["Listed, see https://barracudacentral.org"]
    del r.records[("2.0.0.127.bl.spamcop.net", "A")]  # test point fails: list unusable
    path = tmp_path / "bl.json"
    data = blacklist.update(["203.0.113.10", "2a02:58:1f:10::25"], r, path)
    assert [(x["ip"], x["zone"]) for x in data["listed"]] == [("203.0.113.10", "b.barracudacentral.org")]
    assert data["listed"][0]["reason"].startswith("Listed")
    assert data["unusable"] == ["bl.spamcop.net"]
    v4 = {x["zone"]: x["status"] for x in data["addresses"][0]["results"]}
    assert v4["zen.spamhaus.org"] == "unusable" and v4["psbl.surriel.com"] == "clean"
    v6 = {x["zone"]: x["status"] for x in data["addresses"][1]["results"]}
    assert v6["zen.spamhaus.org"] == "clean" and v6["b.barracudacentral.org"] == "skipped"
    assert data["new_listings"] == [{"ip": "203.0.113.10", "zone": "b.barracudacentral.org"}]

    # Next run keeps "since" and reports nothing new; after delisting it shows as removed
    since = data["listed"][0]["since"]
    data2 = blacklist.update(["203.0.113.10"], r, path)
    assert data2["listed"][0]["since"] == since and data2["new_listings"] == []
    del r.records[("10.113.0.203.b.barracudacentral.org", "A")]
    data3 = blacklist.update(["203.0.113.10"], r, path)
    assert data3["listed"] == [] and data3["removed_listings"] == [{"ip": "203.0.113.10", "zone": "b.barracudacentral.org"}]


def test_classify():
    assert blacklist.classify([]) == "clean"
    assert blacklist.classify(["127.0.0.4"]) == "listed"
    assert blacklist.classify(["127.255.255.252"]) == "unusable"
    assert blacklist.classify(["92.242.132.24"]) == "unusable"  # NXDOMAIN hijack


# --- MX lookup and domain check ---------------------------------------------

def test_mx_lookup():
    r = StaticResolver({
        ("bouwbedrijf-hoorn.nl", "MX"): [(20, "mx2.bouwbedrijf-hoorn.nl"), (10, "mx1.bouwbedrijf-hoorn.nl")],
        ("mx1.bouwbedrijf-hoorn.nl", "A"): ["192.0.2.120"],
        ("mx1.bouwbedrijf-hoorn.nl", "AAAA"): ["2001:db8:120::25"],
        ("mx2.bouwbedrijf-hoorn.nl", "A"): ["192.0.2.121"],
        ("192.0.2.120", "PTR"): ["mx1.bouwbedrijf-hoorn.nl"],
        ("2001:db8:120::25", "PTR"): ["mx1.bouwbedrijf-hoorn.nl"],
        ("bouwbedrijf-hoorn.nl", "TXT"): ["v=spf1 mx include:spf.transip.email ~all"],
        ("_dmarc.bouwbedrijf-hoorn.nl", "TXT"): ["v=DMARC1; p=none"],
    })

    def probe(host, ip):
        return SmtpProbe(host, ip, reachable=True, starttls=host.startswith("mx1"), tls_ok=host.startswith("mx1"))

    res = mx_lookup("Bouwbedrijf-Hoorn.nl.", r, probe=probe)
    assert [m.host for m in res.mx] == ["mx1.bouwbedrijf-hoorn.nl", "mx2.bouwbedrijf-hoorn.nl"]
    m1, m2 = res.mx
    assert m1.ptr_ok and m1.starttls and m1.smtp_ok
    assert not m2.ptr_ok and m2.starttls is False
    codes = {(n["code"], n["host"]) for n in res.notes}
    assert ("no_starttls", "mx2.bouwbedrijf-hoorn.nl") in codes and ("no_ptr", "mx2.bouwbedrijf-hoorn.nl") in codes
    assert res.dmarc_status == "weak"
    d = res.to_dict()
    assert d["mx"][0]["starttls"] is True


def test_mx_lookup_null_and_implicit():
    assert mx_lookup("nomail.example", StaticResolver({("nomail.example", "MX"): [(0, ".")]})).null_mx
    r = mx_lookup("implicit.example", StaticResolver({("implicit.example", "A"): ["192.0.2.5"]}))
    assert r.implicit and r.mx[0].host == "implicit.example"


def test_check_domain():
    r = StaticResolver({
        ("rhis.nl", "MX"): [(10, "pmg.rhis.nl")],
        ("rhis.nl", "TXT"): ["v=spf1 mx -all"],
        ("_dmarc.rhis.nl", "TXT"): ["v=DMARC1; p=none"],
        ("203.0.113.10", "PTR"): ["mailfilter.rhis.nl"],
        ("mailfilter.rhis.nl", "A"): ["203.0.113.10"],
        ("rhis.nl", "MX"): [(10, "pmg.rhis.nl")],
    })
    res = {i.code: i for i in check_domain("rhis.nl", SERVER, r, dkim_key=DkimKey("rhis.nl", "mf2026", "PUB")).items}
    assert res["mx"].status == "missing" and res["mx"].problems == ["mx_elsewhere"]
    assert res["spf"].status == "weak" and res["spf"].fix == "v=spf1 mx include:_spf.mailfilter.rhis.nl -all"
    assert res["dkim"].status == "missing" and res["dkim"].fix == "v=DKIM1; k=rsa; p=PUB"
    assert res["dmarc"].status == "weak"
    assert res["ptr"].status == "bad" and res["ptr"].problems == ["ptr:2a02:58:1f:10::25"]
    assert res["mta_sts"].status == "missing"


# --- config -------------------------------------------------------------------

def test_relay_domains_and_destination(tmp_path):
    (tmp_path / "postfix").mkdir()
    (tmp_path / "postfix" / "relay_domains").write_text("rhis.nl OK\n# c\nexample.nl OK\n")
    (tmp_path / "postfix" / "transport").write_text("rhis.nl relay:[mail.rhis.nl]:25\nexample.nl smtp:mx.example.nl:2525\n")
    doms = relay_domains(tmp_path)
    assert destination_host(doms["rhis.nl"]) == ("mail.rhis.nl", 25)
    assert destination_host(doms["example.nl"]) == ("mx.example.nl", 2525)
    assert destination_host("relay:[2001:db8::5]:25") == ("2001:db8::5", 25)
    assert destination_host(None) is None


def test_server_config(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("MF_"):
            monkeypatch.delenv(k)
    (tmp_path / "mailfilter.env").write_text("MF_HOSTNAME=Mailfilter01.rhis.nl\nMF_PUBLIC_V4=203.0.113.10\nMF_PUBLIC_V6=\n")
    c = ServerConfig.load(tmp_path)
    assert c.hostname == "mailfilter01.rhis.nl" and c.addresses == ["203.0.113.10"]
    assert c.spf_include == "include:_spf.mailfilter01.rhis.nl"


def test_name_matches():
    assert name_matches("mailfilter.rhis.nl", ["*.rhis.nl"])
    assert not name_matches("a.b.rhis.nl", ["*.rhis.nl"])
    assert name_matches("MAILFILTER.rhis.nl.", ["mailfilter.rhis.nl"])
