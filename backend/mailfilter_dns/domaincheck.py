"""The "Check domain" tab: is a domain's mail DNS ready for Mailfilter?"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import dkim as dkim_mod
from . import dmarc as dmarc_mod
from . import spf as spf_mod
from .config import ServerConfig
from .lookup import check_ptr
from .resolver import DnsError, Resolver, norm


@dataclass
class Item:
    code: str  # mx, spf, dkim, dmarc, ptr, mta_sts, tls_rpt
    status: str  # ok, weak, missing, bad, error
    value: str = ""
    problems: list[str] = field(default_factory=list)
    fix: str | None = None  # suggested record value, when Mailfilter can make one


@dataclass
class DomainCheck:
    domain: str
    items: list[Item] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"domain": self.domain, "items": [asdict(i) for i in self.items]}


def _txt(resolver: Resolver, name: str) -> list[str] | DnsError:
    try:
        return resolver.txt(name)
    except DnsError as exc:
        return exc


def check_domain(domain: str, server: ServerConfig, resolver: Resolver,
                 dkim_key: dkim_mod.DkimKey | None | bool = False) -> DomainCheck:
    domain = norm(domain)
    out = DomainCheck(domain)

    # MX points to Mailfilter
    try:
        mxs = resolver.mx(domain)
        value = " ".join(f"{m.priority} {m.host}." for m in mxs)
        hosts = {m.host for m in mxs}
        if not mxs:
            out.items.append(Item("mx", "missing"))
        elif hosts == {server.hostname}:
            out.items.append(Item("mx", "ok", value))
        elif server.hostname in hosts:
            out.items.append(Item("mx", "weak", value, ["mx_mixed"]))
        else:
            out.items.append(Item("mx", "missing", value, ["mx_elsewhere"]))
    except DnsError as exc:
        out.items.append(Item("mx", "error", str(exc)))

    # SPF
    txts = _txt(resolver, domain)
    if isinstance(txts, DnsError):
        out.items.append(Item("spf", "error", str(txts)))
    else:
        rec, extra = spf_mod.find_spf(txts)
        probs = extra + spf_mod.problems(rec)
        if rec and not (spf_mod.contains(rec, server.spf_include) or
                        any(spf_mod.contains(rec, f"ip{'6' if ':' in i else '4'}:{i}") for i in server.addresses)):
            probs.append("mailfilter_missing")
        if rec:
            count = spf_mod.count_lookups(domain, resolver, rec)
            probs += [e for e in count.errors if e.startswith("too_many")]
        status = "missing" if not rec else ("bad" if {"syntax", "plus_all", "multiple_spf", "too_many_lookups"} & set(probs)
                                            else "weak" if probs else "ok")
        fix = None
        if "mailfilter_missing" in probs or not rec:
            try:
                fix = spf_mod.merge(rec, [server.spf_include])
            except spf_mod.SpfSyntaxError:
                fix = None
        out.items.append(Item("spf", status, rec or "", probs, fix))

    # DKIM
    key = dkim_mod.local_key(domain) if dkim_key is False else dkim_key
    res = dkim_mod.verify(key or None, resolver)
    status = {"ok": "ok", "missing": "missing", "no_key": "missing"}.get(res.status, "bad")
    out.items.append(Item("dkim", status, res.published or "", [res.status] if res.status != "ok" else [],
                          key.record if key and res.status != "ok" else None))

    # DMARC
    txts = _txt(resolver, f"_dmarc.{domain}")
    if isinstance(txts, DnsError):
        out.items.append(Item("dmarc", "error", str(txts)))
    else:
        rec, extra = dmarc_mod.find_dmarc(txts)
        st, probs = dmarc_mod.judge(rec)
        fix = None if st == "ok" else dmarc_mod.generate(dmarc_mod.DmarcOptions(policy="quarantine" if rec else "none"))
        out.items.append(Item("dmarc", st, rec or "", probs + extra, fix))

    # Reverse DNS of Mailfilter's own addresses
    bad, vals = [], []
    for ip in server.addresses:
        info = check_ptr(ip, resolver)
        vals.append(f"{ip} → {', '.join(info.ptr) or '—'}")
        if not info.ptr_ok or server.hostname not in info.ptr:
            bad.append(ip)
    out.items.append(Item("ptr", "ok" if server.addresses and not bad else "bad" if bad else "missing",
                          " · ".join(vals), [f"ptr:{ip}" for ip in bad]))

    # MTA-STS and TLS reporting (optional, shown as advice)
    t = _txt(resolver, f"_mta-sts.{domain}")
    sts = [] if isinstance(t, DnsError) else [x for x in t if x.lower().startswith("v=stsv1")]
    out.items.append(Item("mta_sts", "ok" if sts else "missing", sts[0] if sts else ""))
    t = _txt(resolver, f"_smtp._tls.{domain}")
    rpt = [] if isinstance(t, DnsError) else [x for x in t if x.lower().startswith("v=tlsrptv1")]
    out.items.append(Item("tls_rpt", "ok" if rpt else "missing", rpt[0] if rpt else ""))
    return out
