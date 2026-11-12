"""TXT records of a checked domain come from its authoritative name servers."""
from mailfilter_dns.resolver import DnsError, FreshTxt, StaticResolver

CACHED = StaticResolver({
    ("rhis.nl", "TXT"): [],  # the local resolver still remembers "nothing here"
    ("_dmarc.rhis.nl", "TXT"): [],
    ("rhis.nl", "NS"): ["ns1.cloudflare.example", "ns2.cloudflare.example"],
    ("ns1.cloudflare.example", "A"): ["192.0.2.53"],
    ("ns2.cloudflare.example", "AAAA"): ["2001:db8::53"],
    ("other.example", "TXT"): ["v=spf1 -all"],
})
AUTH = StaticResolver({("rhis.nl", "TXT"): ["v=spf1 include:_spf.mf.example ~all"],
                       ("_dmarc.rhis.nl", "TXT"): ["v=DMARC1; p=none"]})


def test_own_records_skip_the_cache():
    made = []
    r = FreshTxt(CACHED, "rhis.nl", make=lambda ips: made.append(ips) or AUTH)
    assert r.txt("rhis.nl") == ["v=spf1 include:_spf.mf.example ~all"]
    assert r.txt("_dmarc.rhis.nl") == ["v=DMARC1; p=none"]
    assert made == [["192.0.2.53", "2001:db8::53"]]  # looked up once
    assert r.txt("other.example") == ["v=spf1 -all"]  # other domains: normal resolver
    assert r.mx("rhis.nl") == []  # everything else is passed through


def test_subdomain_uses_the_zone_of_its_parent():
    r = FreshTxt(CACHED, "nieuwsbrief.rhis.nl", make=lambda ips: AUTH)
    assert r.txt("_dmarc.rhis.nl") == CACHED.txt("_dmarc.rhis.nl")  # outside the checked domain
    assert r._authoritative() is AUTH


def test_falls_back_when_authoritative_servers_fail():
    broken = StaticResolver({("rhis.nl", "TXT"): DnsError("timeout")})
    r = FreshTxt(CACHED, "rhis.nl", make=lambda ips: broken)
    assert r.txt("rhis.nl") == []
    no_ns = FreshTxt(StaticResolver({("rhis.nl", "TXT"): ["x"]}), "rhis.nl")
    assert no_ns.txt("rhis.nl") == ["x"]
