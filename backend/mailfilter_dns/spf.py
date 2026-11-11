"""SPF: parse, generate, merge and count DNS lookups (RFC 7208)."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from .resolver import DnsError, Resolver, norm

# Mechanisms and modifiers that cost a DNS lookup (RFC 7208 section 4.6.4)
LOOKUP_MECHANISMS = {"include", "a", "mx", "ptr", "exists"}
LOOKUP_MODIFIERS = {"redirect"}
MAX_LOOKUPS = 10
MAX_VOID_LOOKUPS = 2

KNOWN_PROVIDERS = {
    "m365": "include:spf.protection.outlook.com",
    "google": "include:_spf.google.com",
}

ALL_QUALIFIERS = {"~": "softfail", "-": "fail", "?": "neutral", "+": "pass"}


@dataclass
class SpfTerm:
    qualifier: str  # "+", "-", "~", "?" ("" for modifiers)
    name: str  # mechanism or modifier name, lower case
    value: str | None  # text after ":" or "=", None if absent
    modifier: bool = False
    cidr: str = ""  # "/24" or "/24//64" on a and mx

    def __str__(self) -> str:
        if self.modifier:
            return f"{self.name}={self.value}"
        q = "" if self.qualifier == "+" else self.qualifier
        return f"{q}{self.name}" + (f":{self.value}" if self.value is not None else "") + self.cidr


@dataclass
class SpfRecord:
    terms: list[SpfTerm] = field(default_factory=list)

    @property
    def mechanisms(self) -> list[SpfTerm]:
        return [t for t in self.terms if not t.modifier]

    @property
    def all_term(self) -> SpfTerm | None:
        return next((t for t in self.terms if not t.modifier and t.name == "all"), None)

    def __str__(self) -> str:
        return " ".join(["v=spf1", *(str(t) for t in self.terms)])


class SpfSyntaxError(ValueError):
    pass


def is_spf(txt: str) -> bool:
    t = txt.strip().lower()
    return t == "v=spf1" or t.startswith("v=spf1 ")


def parse(txt: str) -> SpfRecord:
    if not is_spf(txt):
        raise SpfSyntaxError("not an SPF record (must start with v=spf1)")
    rec = SpfRecord()
    for raw in txt.split()[1:]:
        if "=" in raw and ":" not in raw.split("=", 1)[0]:
            name, value = raw.split("=", 1)
            rec.terms.append(SpfTerm("", name.lower(), value, modifier=True))
            continue
        q = "+"
        if raw[0] in ALL_QUALIFIERS:
            q, raw = raw[0], raw[1:]
        cidr = ""
        name, sep, value = raw.partition(":")
        if "/" in name:  # a/24, mx/24//64
            name, _, rest = name.partition("/")
            cidr, value, sep = "/" + rest, "", ""
        value = value if sep else None
        name = name.lower()
        if name not in {"all", "include", "a", "mx", "ptr", "ip4", "ip6", "exists"}:
            raise SpfSyntaxError(f"unknown mechanism '{name}'")
        if name in {"include", "exists"} and not value:
            raise SpfSyntaxError(f"{name} needs a domain")
        if name in {"ip4", "ip6"}:
            try:
                net = ipaddress.ip_network(value or "", strict=False)
            except ValueError as exc:
                raise SpfSyntaxError(f"invalid address in {name}:{value}") from exc
            if (name == "ip4") != (net.version == 4):
                raise SpfSyntaxError(f"{name}:{value} has the wrong IP version")
        elif value and name in {"a", "mx"} and "/" in value:
            value, _, rest = value.partition("/")
            cidr = "/" + rest
        rec.terms.append(SpfTerm(q, name, value, cidr=cidr))
        continue
    return rec


def find_spf(txts: list[str]) -> tuple[str | None, list[str]]:
    """Return (the SPF record, problems) from a domain's TXT records."""
    spfs = [t for t in txts if is_spf(t)]
    if not spfs:
        return None, []
    if len(spfs) > 1:
        return spfs[0], ["multiple_spf"]
    return spfs[0], []


def generate(
    *,
    includes: list[str] | None = None,
    ip4: list[str] | None = None,
    ip6: list[str] | None = None,
    mx: bool = False,
    a: bool = False,
    all_qualifier: str = "~",
) -> str:
    """Build an SPF record. Order: mx, a, ip4, ip6, includes, all."""
    if all_qualifier not in ALL_QUALIFIERS:
        raise ValueError("all_qualifier must be one of ~ - ? +")
    terms: list[str] = []
    if mx:
        terms.append("mx")
    if a:
        terms.append("a")
    for ip in ip4 or []:
        net = ipaddress.ip_network(ip.strip(), strict=False)
        if net.version != 4:
            raise ValueError(f"{ip} is not an IPv4 address")
        terms.append(f"ip4:{_net_text(net)}")
    for ip in ip6 or []:
        net = ipaddress.ip_network(ip.strip(), strict=False)
        if net.version != 6:
            raise ValueError(f"{ip} is not an IPv6 address")
        terms.append(f"ip6:{_net_text(net)}")
    for inc in includes or []:
        inc = inc.strip()
        inc = KNOWN_PROVIDERS.get(inc, inc)
        if not inc.startswith("include:"):
            inc = "include:" + norm(inc)
        if inc not in terms:
            terms.append(inc)
    terms.append(f"{all_qualifier}all")
    return " ".join(["v=spf1", *dict.fromkeys(terms)])


def _net_text(net: ipaddress._BaseNetwork) -> str:
    return str(net.network_address) if net.prefixlen == net.max_prefixlen else str(net)


def merge(existing: str | None, add: list[str], default_all: str = "~") -> str:
    """Add mechanisms to an existing SPF record without changing anything else.

    New mechanisms go right before ``all`` (or before a ``redirect=``
    modifier), are skipped when already present, and the current ``all``
    qualifier is kept. With no existing record a new one is generated with
    ``default_all``.
    """
    if not existing:
        return str(_from_terms([*add, f"{default_all}all"]))
    rec = parse(existing)
    have = {str(t).lstrip("+").lower() for t in rec.terms}
    new_terms = []
    for item in add:
        t = parse("v=spf1 " + item).terms[0]
        if str(t).lstrip("+").lower() not in have:
            new_terms.append(t)
            have.add(str(t).lstrip("+").lower())
    if not new_terms:
        return str(rec)
    idx = len(rec.terms)
    for i, t in enumerate(rec.terms):
        if (not t.modifier and t.name == "all") or (t.modifier and t.name == "redirect"):
            idx = i
            break
    rec.terms[idx:idx] = new_terms
    return str(rec)


def _from_terms(items: list[str]) -> SpfRecord:
    return parse("v=spf1 " + " ".join(items))


def contains(record: str | None, item: str) -> bool:
    if not record:
        return False
    try:
        rec = parse(record)
    except SpfSyntaxError:
        return False
    want = str(parse("v=spf1 " + item).terms[0]).lstrip("+").lower()
    return any(str(t).lstrip("+").lower() == want for t in rec.terms)


@dataclass
class LookupCount:
    lookups: int = 0
    void_lookups: int = 0
    errors: list[str] = field(default_factory=list)
    tree: list[str] = field(default_factory=list)  # "include:x (3)" style lines


def count_lookups(domain: str, resolver: Resolver, record: str | None = None) -> LookupCount:
    """Count the DNS lookups an SPF check of ``domain`` costs, following includes."""
    result = LookupCount()
    _count(norm(domain), resolver, result, record, depth=0, seen=set())
    if result.lookups > MAX_LOOKUPS:
        result.errors.append("too_many_lookups")
    if result.void_lookups > MAX_VOID_LOOKUPS:
        result.errors.append("too_many_void_lookups")
    return result


def _count(domain, resolver, result, record, depth, seen):
    if depth > 10 or domain in seen:
        result.errors.append(f"loop:{domain}")
        return
    seen = seen | {domain}
    if record is None:
        try:
            record, _ = find_spf(resolver.txt(domain))
        except DnsError as exc:
            result.errors.append(f"dns:{domain}:{exc}")
            return
        if record is None:
            result.void_lookups += 1
            result.errors.append(f"no_spf:{domain}")
            return
    try:
        rec = parse(record)
    except SpfSyntaxError as exc:
        result.errors.append(f"syntax:{domain}:{exc}")
        return
    for t in rec.terms:
        base = t.name
        if t.modifier and base in LOOKUP_MODIFIERS:
            result.lookups += 1
            result.tree.append("  " * depth + str(t))
            _count(norm(t.value or ""), resolver, result, None, depth + 1, seen)
        elif not t.modifier and base in LOOKUP_MECHANISMS:
            result.lookups += 1
            result.tree.append("  " * depth + str(t))
            if base == "include":
                _count(norm(t.value or ""), resolver, result, None, depth + 1, seen)
            elif base == "mx":
                target = norm(t.value or domain)
                try:
                    mxs = resolver.mx(target)
                except DnsError:
                    mxs = []
                if not mxs:
                    result.void_lookups += 1
                if len(mxs) > 10:
                    result.errors.append(f"too_many_mx:{target}")


def problems(record: str | None) -> list[str]:
    """Static problems in an SPF record, as stable codes the UI translates."""
    if not record:
        return ["missing"]
    try:
        rec = parse(record)
    except SpfSyntaxError:
        return ["syntax"]
    out = []
    at = rec.all_term
    if at is None and not any(t.modifier and t.name == "redirect" for t in rec.terms):
        out.append("no_all")
    elif at is not None and at.qualifier == "+":
        out.append("plus_all")
    elif at is not None and at.qualifier == "?":
        out.append("neutral_all")
    if any(t.name == "ptr" for t in rec.mechanisms):
        out.append("ptr_deprecated")
    if len(record) > 450:
        out.append("long")
    return out
