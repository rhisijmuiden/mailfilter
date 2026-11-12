"""Automatic DKIM key rotation, for transport and relay domains.

A key is replaced after ``interval_months`` (default 6). One rotation goes
through these steps, one or more runs of the timer apart:

  publish   A new key with a new selector (mfYYYYMM) is made next to the
            current one. Its record is put in Cloudflare when the domain's
            zone is there; otherwise the admin publishes it by hand. The
            current key keeps signing.
  wait      The new record is in DNS (asked from the domain's own name
            servers). After ``publish_wait_hours`` more the new key signs.
  overlap   The new key signs; the old record stays in DNS for
            ``overlap_days``, so mail signed just before the switch still
            verifies when it is read later.
  (done)    The old record is deleted from Cloudflare (only the record with
            exactly the old public key) and the old private key is deleted.

Only domains whose current key is in DNS rotate automatically, and without
``manual_dns`` only domains in a Cloudflare zone. "Rotate now" starts a
rotation for any domain. In a cluster one node (the first by name) rotates;
the others get the new key through the normal configuration sync.

Per-node state lives in the setting ``dkim_rotation.state``; the settings
in ``dkim_rotation`` are synced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import audit
from .models import Domain
from .models.gateway import DomainRoute
from .settings_store import get_secret, get_setting, set_setting

log = logging.getLogger("mailfilter.dkim_rotation")

CONFIG_KEY = "dkim_rotation"
STATE_KEY = "dkim_rotation.state"
HISTORY_MAX = 50
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "interval_months": 6,
    "overlap_days": 7,
    "publish_wait_hours": 48,
    "manual_dns": False,
}
ACTOR = "dkim-rotation"


# ---- swappable in tests ---------------------------------------------------------------
def helper(request: dict[str, Any]) -> dict[str, Any]:
    from .cluster.snapshot import run_dkim_helper

    return run_dkim_helper(request)


def cloudflare(db: Session):
    """A Cloudflare client when a token is set, else None."""
    from .api import dns as dns_api

    token = get_secret(db, dns_api.TOKEN_KEY)
    return dns_api.make_cloudflare(token) if token else None


def resolver_for(domain: str):
    from mailfilter_dns.resolver import FreshTxt

    from .api import dns as dns_api

    return FreshTxt(dns_api.get_resolver(), domain)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---- settings and state ---------------------------------------------------------------
def config(db: Session) -> dict[str, Any]:
    return {**DEFAULTS, **(get_setting(db, CONFIG_KEY) or {})}


def _state(db: Session) -> dict[str, Any]:
    return dict(get_setting(db, STATE_KEY) or {"domains": {}, "history": []})


def _save(db: Session, st: dict[str, Any]) -> None:
    st["history"] = st.get("history", [])[-HISTORY_MAX:]
    set_setting(db, STATE_KEY, st)


def leader(db: Session) -> str | None:
    """Name of the node that rotates, or None when this server is not in a cluster."""
    from .cluster import state as cluster

    if not cluster.enabled(db):
        return None
    me = cluster.self_node(db)
    names = sorted([n.name for n in cluster.peers(db)] + ([me.name] if me else []))
    return names[0] if names else None


def is_leader(db: Session) -> bool:
    from .cluster import state as cluster

    lead = leader(db)
    me = cluster.self_node(db) if lead else None
    return lead is None or (me is not None and me.name == lead)


# ---- helpers ------------------------------------------------------------------------
def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def public_key(txt: str) -> str | None:
    from mailfilter_dns import dkim

    return dkim.parse_record(dkim.parse_zone_snippet(txt)).get("p") or None


def record_value(txt: str) -> str:
    from mailfilter_dns import dkim

    return dkim.parse_zone_snippet(txt)


def _key(domain: str, selector: str, txt: str):
    from mailfilter_dns import dkim

    tags = dkim.parse_record(dkim.parse_zone_snippet(txt))
    return dkim.DkimKey(domain, selector, tags.get("p", ""), tags.get("k", "rsa"))


def in_dns(domain: str, selector: str, txt: str) -> str:
    """ok, missing, mismatch, revoked, error (see mailfilter_dns.dkim.verify)."""
    from mailfilter_dns import dkim

    if not public_key(txt):
        return "no_key"
    return dkim.verify(_key(domain, selector, txt), resolver_for(domain)).status


def new_selector(at: datetime, taken: set[str]) -> str:
    base = f"mf{at:%Y%m}"
    if base not in taken:
        return base
    for c in "bcdefghijklmnopqrstuvwxyz":
        if base + c not in taken:
            return base + c
    raise ValueError("no free selector")


def due_at(created: datetime | None, months: int) -> datetime | None:
    return created + timedelta(days=round(months * 30.44)) if created else None


def domains(db: Session) -> list[tuple[str, str]]:
    """(name, kind) of the enabled domains."""
    rows = db.execute(select(Domain.name, DomainRoute.kind, DomainRoute.enabled)
                      .join(DomainRoute, DomainRoute.domain_id == Domain.id, isouter=True)
                      .order_by(Domain.name)).all()
    return [(n, k or "transport") for n, k, e in rows if e is not False]


@dataclass
class Zones:
    """Which domains are in a Cloudflare zone; looked up once per run."""

    cf: Any = None
    names: list[str] = field(default_factory=list)
    error: str = ""

    @classmethod
    def load(cls, db: Session) -> "Zones":
        try:
            cf = cloudflare(db)
            if cf is None:
                return cls()
            return cls(cf, [z.name for z in cf.zones()])
        except Exception as e:  # noqa: BLE001 - shown in the UI, rotation waits
            return cls(None, [], str(e)[:300])

    def zone_name(self, domain: str) -> str | None:
        best = None
        for z in self.names:
            if (domain == z or domain.endswith("." + z)) and (best is None or len(z) > len(best)):
                best = z
        return best

    def zone(self, domain: str):
        name = self.zone_name(domain)
        return self.cf.zone_for(name) if (self.cf and name) else None


def _publish(zones: Zones, domain: str, selector: str, txt: str, *, remove: bool = False) -> str:
    """Put the record in Cloudflare (or take it out). Returns '' or an error."""
    from mailfilter_dns import publish

    zone = zones.zone(domain)
    if zone is None:
        return "no_zone"
    key = _key(domain, selector, txt)
    plan = publish.Plan(zone.id, zone.name, domain)
    if remove:
        plan.changes.extend(publish.plan_dkim_remove(zones.cf, zone, key))
    else:
        plan.changes.append(publish.plan_dkim(zones.cf, zone, key))
    if plan.needed:
        publish.apply(zones.cf, plan, actor=ACTOR)
    return ""


def _push_cluster(db: Session) -> None:
    """The signing key changed: make the other nodes take it now."""
    from .cluster import state as cluster

    if cluster.enabled(db):
        cluster.bump_revision(db)
        db.info["cluster_push"] = True  # the web app pushes after the commit
        db.info["dkim_switched"] = True  # the timer pushes itself (see cli.py)


# ---- the run --------------------------------------------------------------------------
@dataclass
class RunResult:
    ran: bool = True
    reason: str = ""
    events: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def status_from_helper() -> dict[str, dict[str, Any]]:
    res = helper({"action": "status"})
    if not res.get("ok"):
        raise RuntimeError(str(res.get("output") or "dkim helper failed")[:300])
    return {d["domain"]: d for d in res.get("domains", [])}


def start(db: Session, domain: str, *, user=None, request=None, zones: Zones | None = None,
          keys: dict[str, dict[str, Any]] | None = None, at: datetime | None = None) -> dict[str, Any]:
    """Make the new key and begin publishing it."""
    at = at or now()
    keys = keys if keys is not None else status_from_helper()
    info = keys.get(domain)
    if not info or not info.get("active"):
        raise ValueError("no_dkim_key")
    st = _state(db)
    if domain in st["domains"]:
        raise ValueError("already_rotating")
    taken = {k["selector"] for k in info.get("keys", [])} | {info["active"]}
    sel = new_selector(at, taken)
    res = helper({"action": "generate", "domain": domain, "selector": sel})
    if not res.get("ok"):
        raise RuntimeError(str(res.get("output") or "generate failed")[:300])
    zones = zones or Zones.load(db)
    auto = zones.zone_name(domain) is not None
    old_txt = next((k.get("txt", "") for k in info.get("keys", []) if k["selector"] == info["active"]), "")
    entry = {"phase": "publish", "old": info["active"], "new": sel, "old_txt": old_txt, "new_txt": res.get("txt", ""),
             "auto": auto, "started_at": _iso(at), "manual": user is not None, "error": ""}
    st["domains"][domain] = entry
    _save(db, st)
    audit(db, request, user, "dkim_rotation_started", domain, old=entry["old"], new=sel, cloudflare=auto)
    try:
        _step(db, domain, entry, zones, at)
    except Exception as ex:  # noqa: BLE001 - e.g. Cloudflare down: the timer tries again
        entry["error"] = str(ex)[:300]
    st = _state(db)
    st["domains"][domain] = entry
    _save(db, st)
    return entry


def cancel(db: Session, domain: str, *, user=None, request=None) -> None:
    st = _state(db)
    entry = st["domains"].get(domain)
    if entry is None:
        raise ValueError("not_rotating")
    if entry["phase"] == "overlap":
        raise ValueError("already_switched")
    if entry.get("auto"):
        zones = Zones.load(db)
        if zones.cf:
            try:
                _publish(zones, domain, entry["new"], entry["new_txt"], remove=True)
            except Exception:  # noqa: BLE001 - an unused record left behind does no harm
                log.warning("could not remove %s._domainkey.%s from Cloudflare", entry["new"], domain)
    helper({"action": "retire", "domain": domain, "selector": entry["new"]})
    del st["domains"][domain]
    _save(db, st)
    audit(db, request, user, "dkim_rotation_cancelled", domain, new=entry["new"])


def _step(db: Session, domain: str, e: dict[str, Any], zones: Zones, at: datetime) -> str | None:
    """Move one rotation forward as far as it can go now. Returns 'done' when finished."""
    cfg = config(db)
    e["error"] = ""
    if e["phase"] == "publish":
        if e.get("auto"):
            if zones.cf is None:
                e["error"] = zones.error or "no_cloudflare"
                return None
            err = _publish(zones, domain, e["new"], e["new_txt"])
            if err:
                e["error"] = err
                return None
            e.setdefault("published_at", _iso(at))
        status = in_dns(domain, e["new"], e["new_txt"])
        e["dns"] = status
        if status != "ok":
            return None
        e["phase"], e["seen_at"] = "wait", _iso(at)
        audit(db, None, None, "dkim_rotation_published", domain, new=e["new"])
    if e["phase"] == "wait":
        seen = _parse(e.get("seen_at")) or at
        if at - seen < timedelta(hours=int(cfg["publish_wait_hours"])):
            return None
        status = in_dns(domain, e["new"], e["new_txt"])
        e["dns"] = status
        if status != "ok":  # gone again: start over at publish
            e["phase"] = "publish"
            return None
        res = helper({"action": "select", "domain": domain, "selector": e["new"]})
        if not res.get("ok"):
            e["error"] = str(res.get("output") or "select failed")[:300]
            return None
        e["phase"], e["switched_at"] = "overlap", _iso(at)
        _push_cluster(db)
        audit(db, None, None, "dkim_rotation_switched", domain, old=e["old"], new=e["new"])
        return None
    if e["phase"] == "overlap":
        switched = _parse(e.get("switched_at")) or at
        if at - switched < timedelta(days=int(cfg["overlap_days"])):
            # A node that synced an older configuration back could have put the old key back
            res = helper({"action": "select", "domain": domain, "selector": e["new"]})
            if res.get("ok") and res.get("changed"):
                _push_cluster(db)
            return None
        old_record = "manual"
        if e.get("auto") and zones.cf is not None:
            try:
                err = _publish(zones, domain, e["old"], e["old_txt"], remove=True)
                old_record = "removed" if not err else "manual"
            except Exception as ex:  # noqa: BLE001
                e["error"] = str(ex)[:300]
                return None
        elif e.get("auto"):
            e["error"] = zones.error or "no_cloudflare"
            return None
        helper({"action": "retire", "domain": domain, "selector": e["old"]})
        e["old_record"] = old_record
        e["finished_at"] = _iso(at)
        audit(db, None, None, "dkim_rotation_finished", domain, old=e["old"], new=e["new"], old_record=old_record)
        return "done"
    return None


def run(db: Session, *, at: datetime | None = None) -> RunResult:
    """One pass of the timer: start due rotations and move running ones forward."""
    at = at or now()
    if not is_leader(db):
        return RunResult(False, f"not_leader:{leader(db)}")
    cfg = config(db)
    out = RunResult()
    try:
        keys = status_from_helper()
    except RuntimeError as ex:
        return RunResult(False, f"helper:{ex}")
    zones = Zones.load(db)
    st = _state(db)

    for domain, entry in list(st["domains"].items()):
        try:
            if _step(db, domain, entry, zones, at) == "done":
                del st["domains"][domain]
                st.setdefault("history", []).append({k: entry.get(k) for k in (
                    "old", "new", "started_at", "switched_at", "finished_at", "old_record", "auto")} | {"domain": domain})
                out.events.append(f"{domain}: finished, {entry['old']} -> {entry['new']}")
            else:
                out.events.append(f"{domain}: {entry['phase']}{' (' + entry['error'] + ')' if entry.get('error') else ''}")
        except Exception as ex:  # noqa: BLE001 - one domain must not stop the others
            entry["error"] = str(ex)[:300]
            out.errors.append(f"{domain}: {ex}")
    _save(db, st)

    if not cfg["enabled"]:
        return out
    for domain, _kind in domains(db):
        info = keys.get(domain)
        if domain in st["domains"] or not info or not info.get("active"):
            continue
        active = next((k for k in info.get("keys", []) if k["selector"] == info["active"]), None)
        if active is None:
            continue
        due = due_at(_parse(active.get("created")), int(cfg["interval_months"]))
        if due is None or due > at:
            continue
        if zones.zone_name(domain) is None and not cfg["manual_dns"]:
            continue
        if in_dns(domain, info["active"], active.get("txt", "")) != "ok":
            continue  # not live (yet): nothing to protect, and nothing would verify anyway
        try:
            start(db, domain, zones=zones, keys=keys, at=at)
            st = _state(db)
            out.events.append(f"{domain}: started, new selector {st['domains'][domain]['new']}")
        except Exception as ex:  # noqa: BLE001
            out.errors.append(f"{domain}: {ex}")
    return out


def overview(db: Session) -> dict[str, Any]:
    """Everything the DKIM keys page shows."""
    cfg = config(db)
    st = _state(db)
    try:
        keys = status_from_helper()
        error = ""
    except RuntimeError as ex:
        keys, error = {}, str(ex)
    zones = Zones.load(db)
    rows = []
    for domain, kind in domains(db):
        info = keys.get(domain) or {}
        active = next((k for k in info.get("keys", []) if k["selector"] == info.get("active")), None)
        created = _parse(active.get("created")) if active else None
        due = due_at(created, int(cfg["interval_months"]))
        rot = st["domains"].get(domain)
        rows.append({
            "domain": domain,
            "kind": kind,
            "selector": info.get("active"),
            "created": active.get("created") if active else None,
            "due": _iso(due) if due else None,
            "cloudflare": zones.zone_name(domain) is not None,
            "record": record_value(active.get("txt", "")) if active else None,
            "rotation": None if rot is None else {
                k: rot.get(k) for k in ("phase", "old", "new", "auto", "started_at", "seen_at", "switched_at",
                                        "error", "dns")
            } | {"new_name": f"{rot['new']}._domainkey.{domain}", "new_record": record_value(rot.get("new_txt", "")),
                 "switch_after": _iso(_parse(rot["seen_at"]) + timedelta(hours=int(cfg["publish_wait_hours"])))
                 if rot.get("seen_at") else None,
                 "finish_after": _iso(_parse(rot["switched_at"]) + timedelta(days=int(cfg["overlap_days"])))
                 if rot.get("switched_at") else None},
        })
    return {
        "config": cfg,
        "leader": leader(db),
        "is_leader": is_leader(db),
        "cloudflare": zones.cf is not None,
        "cloudflare_error": zones.error,
        "error": error,
        "domains": rows,
        "history": list(reversed(st.get("history", [])))[:20],
    }
