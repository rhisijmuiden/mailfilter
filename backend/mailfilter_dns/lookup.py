"""MX lookup for any domain, with addresses, reverse DNS and SMTP/STARTTLS."""

from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import dmarc as dmarc_mod
from . import probe as probe_mod
from . import spf as spf_mod
from .resolver import DnsError, MxRecord, Resolver, norm


@dataclass
class AddressInfo:
    ip: str
    ptr: list[str] = field(default_factory=list)
    ptr_ok: bool = False  # PTR exists and resolves back to this address
    smtp: dict | None = None


@dataclass
class MxHost:
    priority: int
    host: str
    addresses: list[AddressInfo] = field(default_factory=list)
    error: str = ""

    @property
    def smtp_ok(self) -> bool | None:
        res = [a.smtp for a in self.addresses if a.smtp]
        return any(r["reachable"] for r in res) if res else None

    @property
    def starttls(self) -> bool | None:
        res = [a.smtp for a in self.addresses if a.smtp and a.smtp["reachable"]]
        return all(r["starttls"] and r["tls_ok"] for r in res) if res else None

    @property
    def ptr_ok(self) -> bool:
        return bool(self.addresses) and all(a.ptr_ok for a in self.addresses)


@dataclass
class MxLookup:
    domain: str
    mx: list[MxHost] = field(default_factory=list)
    implicit: bool = False  # no MX, mail goes to the A/AAAA of the domain
    null_mx: bool = False  # "0 ." the domain accepts no mail at all
    spf: str | None = None
    spf_problems: list[str] = field(default_factory=list)
    dmarc: str | None = None
    dmarc_status: str = "missing"
    dmarc_problems: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)  # {"level", "code", "host"}
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for m, src in zip(d["mx"], self.mx):
            m.update(smtp_ok=src.smtp_ok, starttls=src.starttls, ptr_ok=src.ptr_ok)
        return d


def same_ip(a: str, b: str) -> bool:
    """True when two textual addresses are the same IP (``::1`` == ``0:0:0:0:0:0:0:1``)."""
    try:
        return ipaddress.ip_address(a.strip()) == ipaddress.ip_address(b.strip())
    except ValueError:
        return a.strip().lower() == b.strip().lower()


def forward_addresses(name: str, resolver: Resolver, ip: str) -> list[str]:
    """A or AAAA records of ``name`` in the family of ``ip``; a failing lookup gives []."""
    try:
        version = ipaddress.ip_address(ip.strip()).version
    except ValueError:
        version = 6 if ":" in ip else 4
    lookups = [resolver.aaaa, resolver.a] if version == 6 else [resolver.a, resolver.aaaa]
    out: list[str] = []
    for fn in lookups:  # each family on its own: a missing AAAA must not hide a good A
        try:
            out += fn(name)
        except DnsError:
            continue
    return out


def check_ptr(ip: str, resolver: Resolver) -> AddressInfo:
    info = AddressInfo(ip)
    try:
        info.ptr = resolver.ptr(ip.strip())
    except DnsError:
        return info
    for name in info.ptr:
        if any(same_ip(ip, back) for back in forward_addresses(name, resolver, ip)):
            info.ptr_ok = True
            break
    return info


ProbeFn = Callable[[str, str], probe_mod.SmtpProbe]


def mx_lookup(domain: str, resolver: Resolver, *, probe: ProbeFn | None = None, max_hosts: int = 6) -> MxLookup:
    """Look up where mail for ``domain`` goes. ``probe`` connects to port 25;
    pass None to skip the network tests."""
    domain = norm(domain)
    res = MxLookup(domain)
    try:
        mxs = resolver.mx(domain)
    except DnsError as exc:
        res.error = str(exc)
        return res
    if len(mxs) == 1 and mxs[0].host in ("", "."):
        res.null_mx = True
        res.notes.append({"level": "info", "code": "null_mx", "host": ""})
    elif not mxs:
        res.implicit = True
        res.notes.append({"level": "warn", "code": "no_mx", "host": domain})
        mxs = [MxRecord(0, domain)]

    for m in mxs[:max_hosts] if not res.null_mx else []:
        host = MxHost(m.priority, m.host)
        try:
            ips = resolver.a(m.host) + resolver.aaaa(m.host)
        except DnsError as exc:
            host.error = str(exc)
            ips = []
        if not ips and not host.error:
            host.error = "no_address"
        host.addresses = [check_ptr(ip, resolver) for ip in ips]
        res.mx.append(host)

    if probe:
        jobs = [(h, a) for h in res.mx for a in h.addresses]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for (h, a), pr in zip(jobs, pool.map(lambda j: probe(j[0].host, j[1].ip), jobs)):
                a.smtp = asdict(pr)

    try:
        res.spf, res.spf_problems = spf_mod.find_spf(resolver.txt(domain))
        res.spf_problems += [p for p in spf_mod.problems(res.spf) if p != "missing" or not res.null_mx]
    except DnsError as exc:
        res.spf_problems = [f"dns:{exc}"]
    try:
        res.dmarc, extra = dmarc_mod.find_dmarc(resolver.txt(f"_dmarc.{domain}"))
        res.dmarc_status, res.dmarc_problems = dmarc_mod.judge(res.dmarc)
        res.dmarc_problems += extra
    except DnsError as exc:
        res.dmarc_problems = [f"dns:{exc}"]

    for h in res.mx:
        if h.error:
            res.notes.append({"level": "bad", "code": h.error if h.error == "no_address" else "dns_error", "host": h.host})
            continue
        if not any(":" in a.ip for a in h.addresses):
            res.notes.append({"level": "info", "code": "no_ipv6", "host": h.host})
        if not h.ptr_ok:
            res.notes.append({"level": "warn", "code": "no_ptr", "host": h.host})
        if h.smtp_ok is False:
            res.notes.append({"level": "bad", "code": "smtp_unreachable", "host": h.host})
        elif h.starttls is False:
            res.notes.append({"level": "bad", "code": "no_starttls", "host": h.host})
        for a in h.addresses:
            c = (a.smtp or {}).get("cert")
            if c and not c["matches"]:
                res.notes.append({"level": "warn", "code": "cert_name", "host": h.host})
                break
            if c and c["days_left"] is not None and c["days_left"] < 0:
                res.notes.append({"level": "bad", "code": "cert_expired", "host": h.host})
                break
    return res


def default_probe(host: str, ip: str) -> probe_mod.SmtpProbe:
    return probe_mod.smtp(host, ip)
