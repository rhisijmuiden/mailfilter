"""What a cluster node shares with the others, and how it is applied.

Synced (the same on every node):
  * domains with their delivery settings, relay networks, extra relay ports,
    throttling (defaults and per destination), address routes, TLS policies
  * users (with password hashes, roles, language and their domains)
  * allow and block lists, and which domains get the daily quarantine report
  * settings listed in SYNCED_SETTINGS (custom rules, hacked-sender protection,
    e-mail sending incl. SMTP password, Cloudflare token)
  * DKIM keys, so every node signs with the key that is in DNS
  * the member list and the cluster secret

Per node (never synced): mail queues and quarantine, mail trace and logs,
audit log, login sessions, backups and their schedule, update channel,
paused senders (each node watches its own traffic), DMARC reports received,
blocklist state, MTA-STS policy files, this node's host name and IPs.

Rows are matched by their natural key (domain name, e-mail address, CIDR...),
never by database id, because ids differ per node. Unknown fields are ignored
and missing ones get their default, so nodes one version apart still sync.

Other features: add a setting key to SYNCED_SETTINGS, or a model to
SYNCED_MODELS plus an export/import pair below, to have it synced.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..models import (AddressRoute, AppSetting, ClusterNode, DestinationPolicy, DigestDomain, Domain, DomainDestination,
                      DomainRoute, RelayNetwork, RelayPort, SenderListEntry, TlsPolicy, User)
from ..settings import get_settings
from ..settings_store import _fernet, set_secret, set_setting
from . import state
from .state import ClusterError

FORMAT = 1

SYNCED_SETTINGS = frozenset({"gateway.defaults", "custom_rules", "abuse", "smtp", "smtp.password", "cloudflare_token",
                             "quarantine_report"})

SYNCED_MODELS = (Domain, DomainRoute, DomainDestination, RelayNetwork, RelayPort, DestinationPolicy, AddressRoute,
                 TlsPolicy, User, ClusterNode, SenderListEntry, DigestDomain)

# Changes to these columns alone are not configuration changes.
LOCAL_COLUMNS = {User: frozenset({"last_login_at", "created_at"}), ClusterNode: ClusterNode.LOCAL,
                 SenderListEntry: frozenset({"created_at"})}
LIST_FIELDS = ("list", "comment", "created_by")

ROUTE_FIELDS = ("enabled", "tls", "verify_recipients", "concurrency", "rate_delay", "out_per_hour", "out_per_day", "note")
DEST_FIELDS = ("host", "port", "mx", "protocol")
USER_FIELDS = ("name", "password_hash", "role", "language", "is_active")

# Simple tables: model, natural key, other fields
SIMPLE = {
    "relay_ports": (RelayPort, "port", ("tls", "description")),
    "destination_policies": (DestinationPolicy, "domain", ("concurrency", "rate_delay", "note")),
    "address_routes": (AddressRoute, "address", ("host", "port", "mx", "protocol", "note")),
    "tls_policies": (TlsPolicy, "destination", ("level", "note")),
}


def _pick(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: getattr(obj, f) for f in fields}


def _put(obj: Any, data: dict[str, Any], fields: tuple[str, ...]) -> None:
    for f in fields:
        if f in data:
            setattr(obj, f, data[f])


# ---- DKIM keys (root helper) --------------------------------------------------------
def run_dkim_helper(request: dict[str, Any]) -> dict[str, Any]:
    """`mailfilter-config dkim --stdin --json` as root. Tests replace this."""
    cmd = [*get_settings().config_helper, "dkim", "--stdin", "--json"]
    try:
        res = subprocess.run(cmd, input=json.dumps(request), capture_output=True, text=True, timeout=120, check=False)
        return json.loads(res.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return {"ok": False, "step": "helper", "output": str(e)}


# ---- export ---------------------------------------------------------------------
def export(db: Session, *, with_dkim: bool = True) -> dict[str, Any]:
    cfg = state.config(db) or {}
    domains = []
    for d in db.execute(select(Domain).order_by(Domain.name)).scalars():
        route = db.get(DomainRoute, d.id)
        domains.append({
            "name": d.name,
            "route": _pick(route, ROUTE_FIELDS) if route else None,
            "destinations": [_pick(x, DEST_FIELDS) for x in route.destinations] if route else [],
        })
    gateway: dict[str, Any] = {
        "domains": domains,
        "relay_networks": [
            {"cidr": n.cidr, "description": n.description, "domain": n.domain.name if n.domain else None,
             "enabled": n.enabled, "allow_broad": n.allow_broad}
            for n in db.execute(select(RelayNetwork).order_by(RelayNetwork.cidr)).scalars()
        ],
    }
    for section, (model, key, fields) in SIMPLE.items():
        gateway[section] = [_pick(r, (key, *fields)) for r in db.execute(select(model).order_by(getattr(model, key))).scalars()]

    lists = [{**_pick(e, ("recipient", "sender", *LIST_FIELDS)), "domain": e.domain.name if e.domain else None}
             for e in db.execute(select(SenderListEntry).order_by(SenderListEntry.recipient, SenderListEntry.sender)).scalars()]
    digest_off = sorted(d.name for d, in db.execute(
        select(Domain).join(DigestDomain, DigestDomain.domain_id == Domain.id).where(DigestDomain.enabled.is_(False))))

    users = [{**_pick(u, USER_FIELDS), "email": u.email, "domains": u.domain_names}
             for u in db.execute(select(User).order_by(User.email)).scalars()]

    settings: dict[str, Any] = {}
    for row in db.execute(select(AppSetting).where(AppSetting.key.in_(SYNCED_SETTINGS))).scalars():
        v = row.value
        if v is None:
            continue
        if isinstance(v, dict) and set(v) == {"enc"}:
            # Encrypted with this node's APP_SECRET: send the plain value (inside the
            # sealed message); the receiver encrypts it with its own.
            v = {"secret": _fernet().decrypt(v["enc"].encode()).decode()}
        settings[row.key] = v

    dkim = None
    if with_dkim:
        res = run_dkim_helper({"action": "export"})
        dkim = res.get("keys") if res.get("ok") else None

    return {
        "format": FORMAT,
        "version": __version__,
        "revision": state.revision(db),
        "cluster": {
            "cluster_id": cfg.get("cluster_id"),
            "spf_name": cfg.get("spf_name", ""),
            "secret": state.secret(db) if cfg else None,
            "nodes": [state.node_info(n) for n in db.execute(select(ClusterNode).order_by(ClusterNode.name)).scalars()],
        },
        "gateway": gateway,
        "users": users,
        "lists": lists,
        "digest_off": digest_off,
        "settings": settings,
        "dkim": dkim,
    }


# ---- import ---------------------------------------------------------------------
def _import_domains(db: Session, items: list[dict]) -> dict[str, Domain]:
    by_name = {d.name.lower(): d for d in db.execute(select(Domain)).scalars()}
    wanted = {str(i["name"]).lower(): i for i in items}
    for name, dom in list(by_name.items()):
        if name not in wanted:
            db.delete(dom)
            del by_name[name]
    db.flush()
    for name, item in wanted.items():
        dom = by_name.get(name)
        if dom is None:
            dom = Domain(name=name)
            db.add(dom)
            db.flush()
            by_name[name] = dom
        route = db.get(DomainRoute, dom.id)
        if item.get("route") is None:
            if route is not None:
                db.delete(route)
            continue
        if route is None:
            route = DomainRoute(domain_id=dom.id)
            db.add(route)
        _put(route, item["route"], ROUTE_FIELDS)
        new = [(x.get("host"), int(x.get("port", 25)), bool(x.get("mx", False)), x.get("protocol", "smtp"))
               for x in item.get("destinations", [])]
        old = [(x.host, x.port, x.mx, x.protocol) for x in route.destinations]
        if new != old:
            route.destinations = [DomainDestination(domain_id=dom.id, position=i, host=h, port=p, mx=m, protocol=pr)
                                  for i, (h, p, m, pr) in enumerate(new)]
    db.flush()
    return by_name


def _import_networks(db: Session, items: list[dict], domains: dict[str, Domain]) -> None:
    have = {n.cidr: n for n in db.execute(select(RelayNetwork)).scalars()}
    wanted = {i["cidr"]: i for i in items}
    for cidr, n in have.items():
        if cidr not in wanted:
            db.delete(n)
    db.flush()
    for cidr, i in wanted.items():
        n = have.get(cidr) or RelayNetwork(cidr=cidr)
        _put(n, i, ("description", "enabled", "allow_broad"))
        owner = domains.get(str(i.get("domain") or "").lower())
        n.domain_id = owner.id if owner else None
        db.add(n)


def _import_simple(db: Session, model, key: str, fields: tuple[str, ...], items: list[dict]) -> None:
    have = {getattr(r, key): r for r in db.execute(select(model)).scalars()}
    wanted = {i[key]: i for i in items}
    for k, r in have.items():
        if k not in wanted:
            db.delete(r)
    db.flush()
    for k, i in wanted.items():
        r = have.get(k) or model(**{key: k})
        _put(r, i, fields)
        db.add(r)


def _import_users(db: Session, items: list[dict], domains: dict[str, Domain]) -> None:
    have = {u.email.lower(): u for u in db.execute(select(User)).scalars()}
    wanted = {str(i["email"]).lower(): i for i in items}
    for email, u in have.items():
        if email not in wanted:
            db.delete(u)
    db.flush()
    for email, i in wanted.items():
        u = have.get(email) or User(email=email)
        _put(u, i, USER_FIELDS)
        u.domains = [domains[d.lower()] for d in i.get("domains", []) if d.lower() in domains]
        db.add(u)


def _import_lists(db: Session, items: list[dict] | None, digest_off: list[str] | None,
                  domains: dict[str, Domain]) -> None:
    if items is not None:  # older nodes do not send lists: leave ours alone
        have = {(e.recipient, e.sender): e for e in db.execute(select(SenderListEntry)).scalars()}
        wanted = {(i["recipient"], i["sender"]): i for i in items}
        for k, e in have.items():
            if k not in wanted:
                db.delete(e)
        db.flush()
        for (rcpt, sender), i in wanted.items():
            e = have.get((rcpt, sender)) or SenderListEntry(recipient=rcpt, sender=sender)
            _put(e, i, LIST_FIELDS)
            owner = domains.get(str(i.get("domain") or "").lower())
            if i.get("domain") and owner is None:
                continue  # its domain is not on this node
            e.domain_id = owner.id if owner else None
            db.add(e)
    if digest_off is not None:
        off = {d.lower() for d in digest_off}
        rows = {r.domain_id: r for r in db.execute(select(DigestDomain)).scalars()}
        for name, dom in domains.items():
            row = rows.get(dom.id)
            if name in off:
                if row is None:
                    db.add(DigestDomain(domain_id=dom.id, enabled=False))
                else:
                    row.enabled = False
            elif row is not None:
                db.delete(row)


def _import_settings(db: Session, items: dict[str, Any]) -> None:
    for key in SYNCED_SETTINGS:
        v = items.get(key)
        if isinstance(v, dict) and set(v) == {"secret"}:
            set_secret(db, key, v["secret"])
        elif v is None:
            if db.get(AppSetting, key) is not None:
                set_setting(db, key, None)
        else:
            set_setting(db, key, v)


def _import_cluster(db: Session, c: dict[str, Any]) -> bool:
    """Member list and secret. Returns False when this node is no longer a member."""
    me = state.self_node(db)
    nodes = {n["node_id"]: n for n in c.get("nodes", [])}
    if me is None or me.node_id not in nodes:
        return False
    have = {n.node_id: n for n in db.execute(select(ClusterNode)).scalars()}
    for nid, n in have.items():
        if nid not in nodes:
            db.delete(n)
    for nid, info in nodes.items():
        n = have.get(nid) or ClusterNode(node_id=nid, is_self=False)
        if not n.is_self:  # our own name/url/addresses are ours to set
            n.name, n.url, n.addresses = str(info["name"])[:100], str(info["url"])[:300], list(info.get("addresses", []))
        db.add(n)
    cfg = dict(state.config(db) or {})
    if c.get("spf_name"):
        cfg["spf_name"] = c["spf_name"]
    set_setting(db, state.CONFIG_KEY, cfg)
    if c.get("secret") and c["secret"] != state.secret(db):
        state.set_cluster_secret(db, c["secret"])
    return True


def apply_snapshot(db: Session, snap: dict[str, Any], *, joining: bool = False) -> str:
    """Make this node's configuration equal to the snapshot. Does not commit.

    Returns "applied" or "removed" (this node is not a member any more; it then
    leaves the cluster and keeps its configuration). Raises ClusterError, or the
    gateway's HTTPException when the mail server refused the configuration."""
    from fastapi import HTTPException

    from ..gateway import IMPORTED_KEY, apply_changes

    if not isinstance(snap, dict) or int(snap.get("format", 0)) > FORMAT:
        raise ClusterError("format", str(snap.get("version", "")) if isinstance(snap, dict) else "")
    cfg = state.config(db) or {}
    cl = snap.get("cluster") or {}
    if cl.get("cluster_id") != cfg.get("cluster_id"):
        raise ClusterError("other_cluster")

    # Not a local change: no new revision, no push. Cleared on commit or rollback.
    db.info["cluster_import"] = True
    if not _import_cluster(db, cl):
        if joining:
            raise ClusterError("not_member")
        state.leave_locally(db)
        return "removed"
    # DKIM first: otherwise applying a new domain would make a key of its own.
    if snap.get("dkim"):
        res = run_dkim_helper({"action": "import", "keys": snap["dkim"]})
        if not res.get("ok"):
            raise ClusterError("dkim_failed", str(res.get("output", ""))[:500])
    g = snap.get("gateway") or {}
    domains = _import_domains(db, g.get("domains", []))
    _import_networks(db, g.get("relay_networks", []), domains)
    for section, (model, key, fields) in SIMPLE.items():
        _import_simple(db, model, key, fields, g.get(section, []))
    _import_users(db, snap.get("users", []), domains)
    _import_lists(db, snap.get("lists"), snap.get("digest_off"), domains)
    _import_settings(db, snap.get("settings") or {})
    set_setting(db, IMPORTED_KEY, {"at": state.now().isoformat(), "from": "cluster"})
    if snap.get("revision"):
        set_setting(db, state.REVISION_KEY, snap["revision"])
    db.flush()
    try:
        apply_changes(db)
    except HTTPException as e:
        raise ClusterError("apply_failed", json.dumps(e.detail, default=str)[:2000]) from None
    if snap.get("lists") is not None:
        from ..lists import write_maps

        try:
            write_maps(db)
        except OSError as e:
            raise ClusterError("apply_failed", f"allow/block lists: {e}") from None
    return "applied"


def count_summary(snap: dict[str, Any]) -> dict[str, int]:
    g = snap.get("gateway") or {}
    return {"domains": len(g.get("domains", [])), "relay_networks": len(g.get("relay_networks", [])),
            "users": len(snap.get("users", [])), "dkim_keys": len(snap.get("dkim") or [])}

