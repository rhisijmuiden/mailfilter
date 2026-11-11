"""One-time go-live per domain: point the MX record in Cloudflare to Mailfilter.

Flow for the UI:
1. ``preflight`` runs the checks and shows them (green/red).
2. ``plan`` shows exactly which MX records change (old value kept).
3. ``go_live`` applies the plan and stores the old records.
4. ``rollback`` puts the old MX records back.

Going live twice is refused; roll back first.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import dkim as dkim_mod
from . import probe as probe_mod
from . import publish
from . import spf as spf_mod
from .cloudflare import Cloudflare, Zone
from .config import STATE, ServerConfig, destination_host, relay_domains
from .resolver import DnsError, Resolver, norm

GOLIVE_DIR = STATE / "golive"


@dataclass
class Check:
    code: str  # accepts, destination, spf, dkim, certificate, host_dns
    ok: bool
    detail: str = ""
    blocking: bool = True


@dataclass
class Preflight:
    domain: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)

    def to_dict(self) -> dict:
        return {"domain": self.domain, "ok": self.ok, "checks": [asdict(c) for c in self.checks]}


SmtpFn = Callable[[str, int], probe_mod.SmtpProbe]
CertFn = Callable[[str], tuple[probe_mod.CertInfo | None, str]]


def preflight(
    domain: str,
    server: ServerConfig,
    resolver: Resolver,
    *,
    domains: dict[str, str | None] | None = None,
    smtp: SmtpFn | None = None,
    cert: CertFn | None = None,
    dkim_key: dkim_mod.DkimKey | None | bool = False,
) -> Preflight:
    domain = norm(domain)
    pf = Preflight(domain)
    domains = relay_domains() if domains is None else domains
    smtp = smtp or (lambda h, p: probe_mod.smtp(h, port=p))
    cert = cert or (lambda h: probe_mod.https_cert(h))

    # 1. Mailfilter accepts mail for the domain
    pf.checks.append(Check("accepts", domain in domains, "" if domain in domains else "not_in_relay_domains"))

    # 2. The destination server answers on its port
    dest = destination_host(domains.get(domain))
    if dest is None:
        pf.checks.append(Check("destination", False, "no_destination"))
    else:
        pr = smtp(*dest)
        pf.checks.append(Check("destination", pr.reachable, f"{dest[0]}:{dest[1]}" + (f" {pr.error}" if pr.error else "")))

    # 3. SPF of the domain includes Mailfilter (warning only: inbound mail does not need it)
    try:
        spf, _ = spf_mod.find_spf(resolver.txt(domain))
    except DnsError as exc:
        spf, _ = None, None
        pf.checks.append(Check("spf", False, str(exc), blocking=False))
    else:
        has = spf_mod.contains(spf, server.spf_include) or any(
            spf_mod.contains(spf, f"ip{'6' if ':' in ip else '4'}:{ip}") for ip in server.addresses
        )
        pf.checks.append(Check("spf", has, spf or "missing", blocking=False))

    # 4. DKIM key published and matching (warning only, signing works without DNS)
    key = dkim_mod.local_key(domain) if dkim_key is False else dkim_key
    res = dkim_mod.verify(key or None, resolver)
    pf.checks.append(Check("dkim", res.status == "ok", res.status, blocking=False))

    # 5. Our own certificate is valid for the host name the MX will point to
    info, err = cert(server.hostname)
    good = bool(info and info.matches and (info.days_left or 0) > 7)
    pf.checks.append(Check("certificate", good, err or (f"{info.not_after}" if info else "")))

    # 6. The host name resolves to this server's addresses
    try:
        ips = set(resolver.a(server.hostname) + resolver.aaaa(server.hostname))
    except DnsError as exc:
        pf.checks.append(Check("host_dns", False, str(exc)))
    else:
        want = set(server.addresses)
        ok = bool(ips) and (not want or bool(ips & want))
        pf.checks.append(Check("host_dns", ok, " ".join(sorted(ips)) or "no_address"))
    return pf


def state_file(domain: str, base: Path = GOLIVE_DIR) -> Path:
    return base / f"{norm(domain)}.json"


def state(domain: str, base: Path = GOLIVE_DIR) -> dict | None:
    try:
        return json.loads(state_file(domain, base).read_text())
    except FileNotFoundError:
        return None


def plan(cf: Cloudflare, domain: str, server: ServerConfig, *, base: Path = GOLIVE_DIR) -> publish.Plan:
    domain = norm(domain)
    st = state(domain, base)
    if st and st.get("status") == "live":
        raise ValueError("already_live")
    zone = cf.zone_for(domain)
    if zone is None:
        raise ValueError("zone_not_found")
    p = publish.Plan(zone.id, zone.name, domain)
    current = cf.records(zone, name=domain, type="MX")
    target = {"type": "MX", "name": domain, "content": server.hostname, "priority": server.mx_priority, "ttl": 1}
    keep = next((r for r in current if norm(r.content) == server.hostname), None)
    for r in current:
        if r is not keep:
            p.changes.append(publish.Change("delete", "MX", domain, r.snapshot(), None, "golive_mx"))
    if keep is None:
        p.changes.append(publish.Change("create", "MX", domain, None, target, "golive_mx"))
    elif keep.priority != server.mx_priority:
        p.changes.append(publish.Change("update", "MX", domain, keep.snapshot(), target, "golive_mx"))
    else:
        p.changes.append(publish.Change("none", "MX", domain, keep.snapshot(), target, "golive_mx"))
    # Create first, then delete, so the domain never has no MX at all
    p.changes.sort(key=lambda c: {"create": 0, "update": 1, "none": 2, "delete": 3}[c.action])
    return p


def go_live(cf: Cloudflare, p: publish.Plan, *, actor: str = "", base: Path = GOLIVE_DIR,
            journal: Path = publish.JOURNAL) -> dict:
    st = state(p.domain, base)
    if st and st.get("status") == "live":
        raise ValueError("already_live")
    done = publish.apply(cf, p, actor=actor, journal=journal)
    record = {
        "domain": p.domain,
        "status": "live",
        "time": _now(),
        "actor": actor,
        "batch": done[0]["batch"] if done else None,
        "previous_mx": [c.before for c in p.changes if c.before and c.action in ("delete", "update")],
        "mx": [c.after for c in p.changes if c.after],
    }
    _write(state_file(p.domain, base), record)
    return record


def rollback(cf: Cloudflare, domain: str, *, actor: str = "", base: Path = GOLIVE_DIR,
             journal: Path = publish.JOURNAL) -> dict:
    st = state(domain, base)
    if not st or st.get("status") != "live":
        raise ValueError("not_live")
    if st.get("batch"):
        publish.undo_batch(cf, st["batch"], actor=actor, journal=journal)
    st.update(status="rolled_back", rolled_back_at=_now(), rolled_back_by=actor)
    _write(state_file(domain, base), st)
    return st


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


__all__ = ["Check", "Preflight", "preflight", "plan", "go_live", "rollback", "state", "Zone"]
