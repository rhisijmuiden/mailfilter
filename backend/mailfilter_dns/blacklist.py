"""Check Mailfilter's own IPv4 and IPv6 addresses against DNS blocklists.

A systemd timer runs ``mailfilter-dns blacklist --update`` every 15 minutes.
The result goes to ``/var/lib/mailfilter/blacklist.json``; the dashboard
shows a big alert as soon as ``listed`` is not empty.

Queries must go through the local unbound resolver: most lists answer
public resolvers (8.8.8.8, 1.1.1.1) with an error code instead of a result.
Each list is first asked for its own test entry (127.0.0.2), so a list that
does not work from this server shows as "unusable" instead of "clean".
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import STATE
from .resolver import DnsError, Resolver, reverse_name

STATUS_FILE = STATE / "blacklist.json"


@dataclass(frozen=True)
class Blocklist:
    zone: str
    name: str
    delist: str
    ipv6: bool = False
    test_v4: str | None = "127.0.0.2"


BLOCKLISTS: list[Blocklist] = [
    Blocklist("zen.spamhaus.org", "Spamhaus ZEN", "https://check.spamhaus.org/", ipv6=True),
    Blocklist("b.barracudacentral.org", "Barracuda", "https://www.barracudacentral.org/rbl/removal-request"),
    Blocklist("bl.spamcop.net", "SpamCop", "https://www.spamcop.net/bl.shtml"),
    Blocklist("dnsbl-1.uceprotect.net", "UCEPROTECT level 1", "https://www.uceprotect.net/en/rblcheck.php"),
    Blocklist("psbl.surriel.com", "PSBL", "https://psbl.org/remove"),
    Blocklist("bl.mailspike.net", "Mailspike", "https://mailspike.org/iplookup.html"),
    Blocklist("ix.dnsbl.manitu.net", "NiX Spam", "https://www.nixspam.net/lookup.php"),
    Blocklist("bl.blocklist.de", "blocklist.de", "https://www.blocklist.de/en/delist.html"),
    Blocklist("all.s5h.net", "s5h.net", "https://www.usenix.org.uk/content/rbl.html", ipv6=True),
]


@dataclass
class ListResult:
    zone: str
    name: str
    status: str  # listed, clean, unusable, error, skipped
    codes: list[str] = field(default_factory=list)
    reason: str = ""
    delist: str = ""
    since: str | None = None


def classify(answers: list[str]) -> str:
    """Turn A answers into listed / clean / unusable.

    127.0.0.x (x>1) means listed. 127.255.255.x is how Spamhaus and others
    say "refused" (public resolver, rate limit). Anything outside 127/8
    usually comes from a resolver that rewrites NXDOMAIN to an ad page.
    """
    if not answers:
        return "clean"
    listed = False
    for a in answers:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            return "unusable"
        if ip.version != 4 or not ip.is_loopback or a.startswith("127.255.255.") or a == "127.0.0.1":
            return "unusable"
        listed = True
    return "listed" if listed else "clean"


def query(ip: str, bl: Blocklist, resolver: Resolver) -> ListResult:
    res = ListResult(bl.zone, bl.name, "skipped", delist=bl.delist)
    if ":" in ip and not bl.ipv6:
        return res
    name = f"{reverse_name(ip)}.{bl.zone}"
    try:
        answers = resolver.a(name)
    except DnsError as exc:
        res.status, res.reason = "error", str(exc)
        return res
    res.status, res.codes = classify(answers), answers
    if res.status == "listed":
        try:
            res.reason = " ".join(resolver.txt(name))[:300]
        except DnsError:
            pass
    return res


def list_works(bl: Blocklist, resolver: Resolver) -> bool:
    if not bl.test_v4:
        return True
    try:
        return classify(resolver.a(f"{reverse_name(bl.test_v4)}.{bl.zone}")) == "listed"
    except DnsError:
        return False


def check(ips: list[str], resolver: Resolver, lists: list[Blocklist] | None = None,
          previous: dict | None = None) -> dict:
    lists = lists or BLOCKLISTS
    now = _now()
    with ThreadPoolExecutor(max_workers=12) as pool:
        works = dict(zip([b.zone for b in lists], pool.map(lambda b: list_works(b, resolver), lists)))
        jobs = [(ip, b) for ip in ips for b in lists]
        results = list(pool.map(lambda j: query(j[0], j[1], resolver) if works[j[1].zone] else
                                ListResult(j[1].zone, j[1].name, "unusable", delist=j[1].delist), jobs))

    prev_since = {}
    for e in (previous or {}).get("addresses", []):
        for r in e.get("results", []):
            if r.get("status") == "listed":
                prev_since[(e["ip"], r["zone"])] = r.get("since")

    out: dict = {"checked_at": now, "addresses": [], "listed": [], "unusable": sorted(z for z, ok in works.items() if not ok)}
    for ip in ips:
        rs = [r for (i, _), r in zip(jobs, results) if i == ip]
        for r in rs:
            if r.status == "listed":
                r.since = prev_since.get((ip, r.zone)) or now
                out["listed"].append({"ip": ip, "zone": r.zone, "name": r.name, "since": r.since,
                                      "reason": r.reason, "delist": r.delist})
        out["addresses"].append({"ip": ip, "results": [asdict(r) for r in rs]})

    before = set(prev_since)
    after = {(x["ip"], x["zone"]) for x in out["listed"]}
    out["new_listings"] = [{"ip": i, "zone": z} for i, z in sorted(after - before)]
    out["removed_listings"] = [{"ip": i, "zone": z} for i, z in sorted(before - after)]
    return out


def load(path: Path = STATUS_FILE) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def save(data: dict, path: Path = STATUS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o644)
    tmp.replace(path)


def update(ips: list[str], resolver: Resolver, path: Path = STATUS_FILE) -> dict:
    data = check(ips, resolver, previous=load(path))
    save(data, path)
    return data


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
