"""Publish SPF, DKIM and DMARC to Cloudflare, always as a reviewable plan.

Every function here first builds a list of ``Change`` objects (what is in
Cloudflare now, what it will be). The UI shows that list, and only
``apply`` writes. Each applied change is appended to a journal with the old
value, so any change can be undone.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import dkim as dkim_mod
from . import spf as spf_mod
from .cloudflare import Cloudflare, DnsRecord, Zone
from .config import STATE, ServerConfig
from .resolver import norm

JOURNAL = STATE / "dns-journal.jsonl"
COMMENT = "Managed by Mailfilter"


@dataclass
class Change:
    action: str  # create, update, delete, none
    type: str
    name: str
    before: dict | None = None  # record snapshot as it is in Cloudflare
    after: dict | None = None  # record body that will be written
    reason: str = ""  # stable code: spf_merge, dkim, dmarc, golive_mx, ...

    @property
    def noop(self) -> bool:
        return self.action == "none"


@dataclass
class Plan:
    zone_id: str
    zone_name: str
    domain: str
    changes: list[Change] = field(default_factory=list)

    @property
    def needed(self) -> list[Change]:
        return [c for c in self.changes if not c.noop]

    def to_dict(self) -> dict:
        return asdict(self)


def _txt_records(cf: Cloudflare, zone: Zone, name: str) -> list[DnsRecord]:
    return cf.records(zone, name=name, type="TXT")


def _txt_change(existing: DnsRecord | None, name: str, value: str, reason: str) -> Change:
    after = {"type": "TXT", "name": name, "content": value, "ttl": existing.ttl if existing else 1}
    if existing is None:
        return Change("create", "TXT", name, None, after, reason)
    if existing.content == value:
        return Change("none", "TXT", name, existing.snapshot(), after, reason)
    return Change("update", "TXT", name, existing.snapshot(), after, reason)


def plan_spf(cf: Cloudflare, zone: Zone, domain: str, add: list[str], default_all: str = "~") -> Change:
    """Add Mailfilter to the domain's SPF, keeping everything that is there."""
    domain = norm(domain)
    spfs = [r for r in _txt_records(cf, zone, domain) if spf_mod.is_spf(r.content)]
    if len(spfs) > 1:
        raise ValueError("multiple_spf")
    cur = spfs[0] if spfs else None
    return _txt_change(cur, domain, spf_mod.merge(cur.content if cur else None, add, default_all), "spf_merge")


def plan_spf_value(cf: Cloudflare, zone: Zone, domain: str, value: str) -> Change:
    """Replace the SPF with a value from the generator."""
    spf_mod.parse(value)
    domain = norm(domain)
    spfs = [r for r in _txt_records(cf, zone, domain) if spf_mod.is_spf(r.content)]
    if len(spfs) > 1:
        raise ValueError("multiple_spf")
    return _txt_change(spfs[0] if spfs else None, domain, value, "spf")


def plan_server_spf(cf: Cloudflare, zone: Zone, server: ServerConfig) -> Change:
    """The shared record (``_spf.<host>``) that lists every Mailfilter address.

    Domains only include this name, so adding a second server later means
    changing one record instead of every domain.
    """
    value = spf_mod.generate(ip4=server.public_v4, ip6=server.public_v6, all_qualifier="-")
    cur = next((r for r in _txt_records(cf, zone, server.spf_name) if spf_mod.is_spf(r.content)), None)
    return _txt_change(cur, server.spf_name, value, "server_spf")


def plan_dkim(cf: Cloudflare, zone: Zone, key: dkim_mod.DkimKey) -> Change:
    recs = [r for r in _txt_records(cf, zone, key.name) if "p=" in r.content.replace(" ", "")]
    cur = recs[0] if recs else None
    if cur and dkim_mod.parse_record(cur.content).get("p") == key.public_key:
        return Change("none", "TXT", key.name, cur.snapshot(), cur.snapshot(), "dkim")
    return _txt_change(cur, key.name, key.record, "dkim")


def plan_dmarc(cf: Cloudflare, zone: Zone, domain: str, value: str) -> Change:
    from . import dmarc as dmarc_mod

    dmarc_mod.parse(value)
    name = f"_dmarc.{norm(domain)}"
    recs = [r for r in _txt_records(cf, zone, name) if dmarc_mod.is_dmarc(r.content)]
    if len(recs) > 1:
        raise ValueError("multiple_dmarc")
    return _txt_change(recs[0] if recs else None, name, value, "dmarc")


def plan_mx(cf: Cloudflare, zone: Zone, domain: str, targets: list[tuple[int, str]]) -> list[Change]:
    """Make the domain's MX records in Cloudflare exactly ``targets``
    ((priority, host) pairs). Records whose host stays are kept or get a new
    priority; new hosts are created before old ones are deleted, so the domain
    never has no MX at all."""
    domain = norm(domain)
    want: dict[str, int] = {}
    for prio, host in targets:
        want.setdefault(norm(host), int(prio))
    if not want:
        raise ValueError("no_mx")
    current = cf.records(zone, name=domain, type="MX")
    changes: list[Change] = []
    seen: set[str] = set()
    for r in current:
        host = norm(r.content)
        if host in want and host not in seen:
            seen.add(host)
            after = {"type": "MX", "name": domain, "content": host, "priority": want[host], "ttl": r.ttl}
            changes.append(Change("none" if r.priority == want[host] else "update", "MX", domain,
                                  r.snapshot(), after, "mx"))
        else:
            changes.append(Change("delete", "MX", domain, r.snapshot(), None, "mx"))
    for host, prio in want.items():
        if host not in seen:
            changes.append(Change("create", "MX", domain, None,
                                  {"type": "MX", "name": domain, "content": host, "priority": prio, "ttl": 1}, "mx"))
    changes.sort(key=lambda c: {"create": 0, "update": 1, "none": 2, "delete": 3}[c.action])
    return changes


def apply(cf: Cloudflare, plan: Plan, *, actor: str = "", journal: Path | None = None, undo_of: str | None = None) -> list[dict]:
    """Write the plan to Cloudflare. Stops at the first error; what was done
    before that is in the journal and in the returned list."""
    journal = journal or JOURNAL
    zone = Zone(plan.zone_id, plan.zone_name)
    batch = uuid.uuid4().hex[:12]
    done: list[dict] = []
    for ch in plan.needed:
        if ch.action == "delete":
            cf.delete(zone, (ch.before or {})["id"])
            result = None
        else:
            a = ch.after or {}
            rec = DnsRecord(a["type"], a["name"], a["content"], a.get("ttl", 1), a.get("priority"),
                            a.get("proxied"), comment=COMMENT)
            if ch.action == "update":
                rec.id = (ch.before or {})["id"]
                result = cf.update(zone, rec).snapshot()
            else:
                result = cf.create(zone, rec).snapshot()
        entry = {"time": _now(), "batch": batch, "actor": actor, "zone_id": zone.id, "zone": zone.name,
                 "domain": plan.domain, "action": ch.action, "reason": ch.reason, "before": ch.before,
                 "after": result}
        if undo_of:
            entry["undo"] = undo_of
        _journal(journal, entry)
        done.append(entry)
    return done


def undo_batch(cf: Cloudflare, batch: str, *, actor: str = "", journal: Path | None = None) -> list[dict]:
    """Put back the records a batch changed, newest first."""
    journal = journal or JOURNAL
    entries = [e for e in read_journal(journal) if e.get("batch") == batch and not e.get("undo")]
    if not entries:
        raise ValueError("unknown_batch")
    if any(e.get("undo") == batch for e in read_journal(journal)):
        raise ValueError("already_undone")
    zone = Zone(entries[0]["zone_id"], entries[0]["zone"])
    plan = Plan(zone.id, zone.name, entries[0]["domain"])
    for e in reversed(entries):
        before, after = e.get("before"), e.get("after")
        if e["action"] == "create" and after:
            plan.changes.append(Change("delete", after["type"], after["name"], after, None, "undo"))
        elif e["action"] == "update" and before and after:
            plan.changes.append(Change("update", before["type"], before["name"], after,
                                       {**before, "id": None}, "undo"))
        elif e["action"] == "delete" and before:
            plan.changes.append(Change("create", before["type"], before["name"], None, before, "undo"))
    return apply(cf, plan, actor=actor, journal=journal, undo_of=batch)


def read_journal(journal: Path | None = None, limit: int | None = None) -> list[dict]:
    journal = journal or JOURNAL
    try:
        lines = journal.read_text().splitlines()
    except FileNotFoundError:
        return []
    out = [json.loads(ln) for ln in lines if ln.strip()]
    return out[-limit:] if limit else out


def _journal(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if os.geteuid() == 0:
        # Run from the CLI as root: keep the file writable for the web app
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
