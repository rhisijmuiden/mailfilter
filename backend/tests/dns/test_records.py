import pytest

from mailfilter_dns import dkim, dmarc, spf
from mailfilter_dns.resolver import DnsError, StaticResolver, reverse_name


# --- SPF -------------------------------------------------------------------

def test_spf_parse_roundtrip():
    v = "v=spf1 mx a/24 ip4:192.0.2.0/24 ip6:2001:db8::/32 include:spf.protection.outlook.com -all"
    assert str(spf.parse(v)) == v


def test_spf_parse_cidr_and_modifiers():
    rec = spf.parse("v=spf1 a:mail.example.com/28 mx/24//64 redirect=_spf.example.com")
    assert [str(t) for t in rec.terms] == ["a:mail.example.com/28", "mx/24//64", "redirect=_spf.example.com"]
    assert rec.terms[-1].modifier


@pytest.mark.parametrize("bad", ["v=spf2 mx", "v=spf1 foo", "v=spf1 ip4:300.1.1.1", "v=spf1 ip4:2001:db8::1", "v=spf1 include"])
def test_spf_parse_errors(bad):
    with pytest.raises(spf.SpfSyntaxError):
        spf.parse(bad)


def test_spf_merge_keeps_existing_and_all():
    cur = "v=spf1 mx include:spf.transip.email -all"
    assert spf.merge(cur, ["include:_spf.mailfilter.rhis.nl"]) == \
        "v=spf1 mx include:spf.transip.email include:_spf.mailfilter.rhis.nl -all"


def test_spf_merge_is_idempotent_and_handles_redirect():
    cur = "v=spf1 include:_spf.mailfilter.rhis.nl ~all"
    assert spf.merge(cur, ["include:_spf.mailfilter.rhis.nl"]) == cur
    assert spf.merge("v=spf1 mx redirect=_spf.example.com", ["ip4:192.0.2.1"]) == \
        "v=spf1 mx ip4:192.0.2.1 redirect=_spf.example.com"
    assert spf.merge(None, ["include:x.example"]) == "v=spf1 include:x.example ~all"


def test_spf_generate():
    v = spf.generate(includes=["m365", "_spf.mailfilter.rhis.nl"], ip4=["203.0.113.25", "198.51.100.0/24"],
                     ip6=["2a02:58:1f:42::/64"], mx=True, all_qualifier="-")
    assert v == ("v=spf1 mx ip4:203.0.113.25 ip4:198.51.100.0/24 ip6:2a02:58:1f:42::/64 "
                 "include:spf.protection.outlook.com include:_spf.mailfilter.rhis.nl -all")
    with pytest.raises(ValueError):
        spf.generate(ip4=["2001:db8::1"])


def test_spf_problems():
    assert spf.problems(None) == ["missing"]
    assert spf.problems("v=spf1 a mx +all") == ["plus_all"]
    assert spf.problems("v=spf1 ptr ~all") == ["ptr_deprecated"]
    assert spf.problems("v=spf1 mx") == ["no_all"]
    assert spf.problems("v=spf1 mx -all") == []


def test_spf_lookup_count_follows_includes():
    r = StaticResolver({
        ("a.example", "TXT"): ["v=spf1 include:b.example include:c.example mx -all"],
        ("b.example", "TXT"): ["v=spf1 include:c.example ip4:192.0.2.1 -all"],
        ("c.example", "TXT"): ["v=spf1 a -all"],
        ("a.example", "MX"): [(10, "mx.a.example")],
    })
    res = spf.count_lookups("a.example", r)
    # include b, include c (in b), a (in c), include c, a (in c), mx = 6
    assert res.lookups == 6
    assert res.errors == []


def test_spf_lookup_count_too_many_and_void():
    recs = {("x.example", "TXT"): ["v=spf1 " + " ".join(f"include:i{n}.example" for n in range(11)) + " -all"]}
    for n in range(11):
        recs[(f"i{n}.example", "TXT")] = ["v=spf1 -all"]
    assert "too_many_lookups" in spf.count_lookups("x.example", StaticResolver(recs)).errors
    r = StaticResolver({("y.example", "TXT"): ["v=spf1 include:a.invalid include:b.invalid include:c.invalid -all"]})
    assert "too_many_void_lookups" in spf.count_lookups("y.example", r).errors


def test_find_spf_multiple():
    rec, probs = spf.find_spf(["google-site-verification=x", "v=spf1 -all", "v=spf1 mx -all"])
    assert rec == "v=spf1 -all" and probs == ["multiple_spf"]


# --- DMARC -----------------------------------------------------------------

def test_dmarc_generate_and_parse():
    v = dmarc.generate(dmarc.DmarcOptions("quarantine", "reject", 50, ["dmarc@rhis.nl"], None, "s", "r"))
    assert v == "v=DMARC1; p=quarantine; sp=reject; pct=50; rua=mailto:dmarc@rhis.nl; adkim=s"
    tags = dmarc.parse(v)
    assert tags["p"] == "quarantine" and dmarc.report_domains(tags) == ["rhis.nl"]


@pytest.mark.parametrize("bad", ["v=DMARC1", "v=DMARC1; p=block", "v=DMARC1; p=none; pct=150", "p=none"])
def test_dmarc_parse_errors(bad):
    with pytest.raises(dmarc.DmarcSyntaxError):
        dmarc.parse(bad)


def test_dmarc_judge():
    assert dmarc.judge(None) == ("missing", ["missing"])
    assert dmarc.judge("v=DMARC1; p=none")[0] == "weak"
    assert dmarc.judge("v=DMARC1; p=reject; rua=mailto:a@b.nl") == ("ok", [])
    assert dmarc.judge("v=DMARC1; p=quarantine")[0] == "ok"
    assert dmarc.judge("v=DMARC1; p=bogus") == ("bad", ["syntax"])


# --- DKIM ------------------------------------------------------------------

def _rsa_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    k = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def test_dkim_local_key_from_private_and_snippet(tmp_path):
    etc, keys = tmp_path / "etc", tmp_path / "keys"
    (etc / "rspamd").mkdir(parents=True)
    keys.mkdir()
    (etc / "rspamd" / "dkim_selectors.map").write_text("rhis.nl mf2026\n")
    pem = _rsa_pem()
    (keys / "rhis.nl.mf2026.key").write_bytes(pem)
    k = dkim.local_key("rhis.nl", etc=etc, key_dir=keys)
    assert k and k.selector == "mf2026" and k.name == "mf2026._domainkey.rhis.nl"
    _, pub = dkim.public_key_from_private_pem(pem)
    assert k.public_key == pub and k.record == f"v=DKIM1; k=rsa; p={pub}"

    # Without access to the private key the installer's zone snippet is used
    (keys / "rhis.nl.mf2026.key").unlink()
    (etc / "dkim-rhis.nl.mf2026.txt").write_text(
        f'mf2026._domainkey IN TXT ( "v=DKIM1; k=rsa; "\n\t"p={pub[:100]}"\n\t"{pub[100:]}" ) ;\n')
    assert dkim.local_key("rhis.nl", etc=etc, key_dir=keys).public_key == pub
    assert dkim.local_key("other.nl", etc=etc, key_dir=keys) is None


def test_dkim_verify():
    key = dkim.DkimKey("rhis.nl", "mf2026", "ABC")
    name = key.name
    assert dkim.verify(key, StaticResolver({(name, "TXT"): ["v=DKIM1; k=rsa; p=ABC"]})).status == "ok"
    assert dkim.verify(key, StaticResolver({(name, "TXT"): ["v=DKIM1; k=rsa; p=XYZ"]})).status == "mismatch"
    assert dkim.verify(key, StaticResolver({(name, "TXT"): ["v=DKIM1; p="]})).status == "revoked"
    assert dkim.verify(key, StaticResolver()).status == "missing"
    assert dkim.verify(None, StaticResolver()).status == "no_key"
    assert dkim.verify(key, StaticResolver({(name, "TXT"): DnsError("timeout")})).status == "error"


def test_reverse_name():
    assert reverse_name("192.0.2.10") == "10.2.0.192"
    assert reverse_name("2001:db8::1").startswith("1.0.0.0.") and reverse_name("2001:db8::1").endswith("8.b.d.0.1.0.0.2")


def test_dmarc_merge_adds_rua_and_keeps_tags():
    cur = "v=DMARC1; p=none; rua=mailto:x@dmarcian.com; fo=1"
    assert dmarc.merge(cur, add_rua="dmarc-reports@mailfilter.rhis.nl") == \
        "v=DMARC1; p=none; rua=mailto:x@dmarcian.com,mailto:dmarc-reports@mailfilter.rhis.nl; fo=1"
    # idempotent, also with a size limit on our address
    again = "v=DMARC1; p=none; rua=mailto:dmarc-reports@mailfilter.rhis.nl!10m"
    assert dmarc.merge(again, add_rua="dmarc-reports@mailfilter.rhis.nl") == again
    assert dmarc.merge("v=DMARC1; p=quarantine; sp=none", add_rua="a@b.nl") == "v=DMARC1; p=quarantine; sp=none; rua=mailto:a@b.nl"
    assert dmarc.merge(None, add_rua="a@b.nl") == "v=DMARC1; p=none; rua=mailto:a@b.nl"


def test_dmarc_merge_policy_drops_pct():
    assert dmarc.merge("v=DMARC1; p=quarantine; pct=25; rua=mailto:a@b.nl", policy="reject") == \
        "v=DMARC1; p=reject; rua=mailto:a@b.nl"
    with pytest.raises(dmarc.DmarcSyntaxError):
        dmarc.merge("v=DMARC1; p=bogus", policy="reject")
    assert dmarc.rua_addresses({"rua": "mailto:A@B.nl!5m, https://x"}) == ["a@b.nl"]
