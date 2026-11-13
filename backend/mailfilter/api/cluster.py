"""System > Cluster: create, join or leave a cluster, see the other nodes.

/api/cluster/...        admin screens (logged in)
/api/cluster/peer/<op>  the other nodes (no login; every body is sealed with the
                        cluster secret, see cluster/state.py)
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import __version__
from ..audit import audit
from ..cluster import peer, state
from ..cluster import tracking
from ..cluster.snapshot import SYNCED_SETTINGS, export
from ..cluster.state import ClusterError
from ..deps import DB, AdminUser, CurrentUser
from ..models import ClusterNode
from ..settings_store import get_setting, set_setting

tracking.install()  # configuration changes made through the web app reach the other nodes

router = APIRouter(prefix="/cluster", tags=["cluster"])
public = APIRouter(prefix="/cluster/peer", tags=["cluster"])


def _err(e: ClusterError, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, {"code": f"cluster_{e.code}", "detail": e.detail})


# ---- admin screens -----------------------------------------------------------------
class NodeOut(BaseModel):
    node_id: str
    name: str
    url: str
    addresses: list[str]
    is_self: bool
    joined_at: str | None
    last_seen_at: str | None
    last_sync_at: str | None
    last_error: str
    last_error_at: str | None
    version: str
    in_sync: bool
    latency_ms: int | None
    state: str  # self, ok, behind, unreachable, error, waiting
    role: str  # member, primary, standby
    failover: dict[str, Any] | None  # a standby's failover state, as it reported it


def _iso(v) -> str | None:
    return v.isoformat() if v else None


def _node_out(n: ClusterNode, mine: dict | None) -> NodeOut:
    now = state.now()
    in_sync = n.is_self or state.rev_key(n.revision) == state.rev_key(mine)
    if n.is_self:
        st = "self"
    elif n.last_error and (not n.last_seen_at or (n.last_error_at and n.last_error_at > n.last_seen_at)):
        st = "unreachable" if n.last_error.startswith("unreachable") else "error"
    elif not n.last_seen_at:
        st = "waiting"
    elif n.last_seen_at < now - timedelta(minutes=5):
        st = "unreachable"
    else:
        st = "ok" if in_sync else "behind"
    return NodeOut(node_id=n.node_id, name=n.name, url=n.url, addresses=list(n.addresses or []), is_self=n.is_self,
                   joined_at=_iso(n.joined_at), last_seen_at=_iso(n.last_seen_at), last_sync_at=_iso(n.last_sync_at),
                   last_error=n.last_error or "", last_error_at=_iso(n.last_error_at),
                   version=__version__ if n.is_self else n.version, in_sync=in_sync, latency_ms=n.latency_ms, state=st,
                   role=n.role or "member", failover=(n.info or {}).get("failover") if not n.is_self else None)


def _overview(db) -> dict[str, Any]:
    from ..cluster.failover import current_state

    cfg = state.config(db)
    mine = state.revision(db)
    nodes = [_node_out(n, mine) for n in db.execute(
        select(ClusterNode).order_by(ClusterNode.is_self.desc(), ClusterNode.name)).scalars()]
    p = state.primary(db)
    return {
        "mode": state.mode(db),
        "role": state.my_role(db),
        "primary": p.name if p else None,
        "primary_url": p.url if p else None,
        "failover": state.failover_settings(db),
        "failover_state": current_state(db) if state.my_role(db) == "standby" else None,
        "enabled": bool(cfg),
        "cluster_id": (cfg or {}).get("cluster_id"),
        "spf_name": (cfg or {}).get("spf_name", ""),
        "revision": mine,
        "last_import": get_setting(db, state.LAST_IMPORT_KEY),
        "nodes": [n.model_dump() for n in nodes],
        "versions_differ": len({n.version for n in nodes if n.version}) > 1,
        "default_name": state.default_name(),
        "own_url": state.own_url(),
        "synced_settings": sorted(SYNCED_SETTINGS),
    }


@router.get("/status")
def cluster_status(_: CurrentUser, db: DB) -> dict[str, Any]:
    """For the banner on every page: is this a backup server, and is it standing in?"""
    if not state.enabled(db):
        return {"enabled": False}
    from ..cluster.failover import current_state

    p = state.primary(db)
    return {"enabled": True, "mode": state.mode(db), "role": state.my_role(db), "primary": p.name if p else None,
            "primary_url": p.url if p else None, "failover_active": bool(current_state(db).get("active"))}


@router.get("")
def overview(_: AdminUser, db: DB) -> dict[str, Any]:
    return _overview(db)


class CreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    # active: servers work together; standby: this server is the primary, the next ones are backups
    mode: Literal["active", "standby"] = "active"


def _check_name(name: str) -> str:
    name = name.strip()
    if not state.NAME_RE.match(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": "cluster_bad_name", "detail": ""})
    return name


@router.post("/create")
def create(body: CreateIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    from mailfilter_dns.config import ServerConfig

    if state.enabled(db):
        raise _err(ClusterError("already_in_cluster"))
    url = state.own_url()
    if not url:
        raise _err(ClusterError("no_hostname"))
    node_id = state.new_id()
    set_setting(db, state.CONFIG_KEY, {"cluster_id": state.new_id(), "node_id": node_id, "mode": body.mode,
                                       "spf_name": ServerConfig.load().spf_name, "created_at": state.now().isoformat()})
    state.set_cluster_secret(db, state.new_secret())
    db.add(ClusterNode(node_id=node_id, name=_check_name(body.name), url=url, addresses=state.own_addresses(),
                       is_self=True, role="primary" if body.mode == state.STANDBY else "member"))
    state.bump_revision(db)
    audit(db, request, admin, "cluster.create", body.name, mode=body.mode)
    db.commit()
    return _overview(db)


@router.get("/join-code")
def get_join_code(request: Request, admin: AdminUser, db: DB) -> dict[str, str]:
    if state.my_role(db) == "standby":
        raise _err(ClusterError("standby_readonly", (state.primary(db) or ClusterNode(name="")).name), 409)
    try:
        code = state.join_code(db)
    except ClusterError as e:
        raise _err(e) from None
    audit(db, request, admin, "cluster.join_code_shown")
    db.commit()
    return {"code": code}


class JoinIn(BaseModel):
    code: str = Field(min_length=10, max_length=2000)
    name: str = Field(min_length=1, max_length=63)


@router.post("/join")
def join(body: JoinIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    name = _check_name(body.name)
    email = admin.email
    try:
        snap = peer.join(db, body.code, name)
    except ClusterError as e:
        raise _err(e) from None
    audit(db, request, None, "cluster.join", name, by=email, domains=len((snap.get("gateway") or {}).get("domains", [])))
    db.commit()
    return _overview(db)


@router.post("/leave")
def leave(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    try:
        peer.leave(db)
    except ClusterError as e:
        raise _err(e) from None
    audit(db, request, admin, "cluster.leave")
    db.commit()
    return _overview(db)


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=63)


@router.put("/self")
def rename_self(body: RenameIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    me = state.self_node(db)
    if me is None:
        raise _err(ClusterError("not_in_cluster"))
    me.name = _check_name(body.name)
    me.url = state.own_url() or me.url
    me.addresses = state.own_addresses() or me.addresses
    audit(db, request, admin, "cluster.rename", me.name)
    db.commit()
    return _overview(db)


@router.delete("/nodes/{node_id}")
def remove_node(node_id: str, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    """Remove another node (e.g. a server that is gone). The cluster secret is
    replaced so the removed node cannot connect any more."""
    n = db.get(ClusterNode, node_id)
    if n is None or n.is_self:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    db.delete(n)
    state.set_cluster_secret(db, state.new_secret())
    audit(db, request, admin, "cluster.remove_node", n.name)
    db.commit()
    return _overview(db)


@router.post("/sync")
def sync_now(_: AdminUser, db: DB) -> dict[str, Any]:
    if not state.enabled(db):
        raise _err(ClusterError("not_in_cluster"))
    results = peer.sync_all(db)
    return {**_overview(db), "results": results}


# ---- backup mode ------------------------------------------------------------------
def _not_on_standby(db) -> None:
    if state.my_role(db) == "standby":
        raise _err(ClusterError("standby_readonly", (state.primary(db) or ClusterNode(name="")).name), 409)


class ModeIn(BaseModel):
    mode: Literal["active", "standby"]


@router.put("/mode")
def set_mode(body: ModeIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    """Switch between working together and primary + backup. This server becomes the primary."""
    if not state.enabled(db):
        raise _err(ClusterError("not_in_cluster"))
    _not_on_standby(db)
    cfg = dict(state.config(db) or {})
    cfg["mode"] = body.mode
    set_setting(db, state.CONFIG_KEY, cfg)
    for n in db.execute(select(ClusterNode)).scalars():
        n.role = ("primary" if n.is_self else "standby") if body.mode == state.STANDBY else "member"
    tracking.mark_changed(db)
    audit(db, request, admin, "cluster.mode", body.mode)
    db.commit()
    return _overview(db)


class FailoverIn(BaseModel):
    enabled: bool = False
    hostname: str = Field(default="", max_length=253)
    after_minutes: int = Field(default=5, ge=2, le=120)
    back_minutes: int = Field(default=10, ge=2, le=1440)


@router.put("/failover")
def set_failover(body: FailoverIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    if not state.enabled(db):
        raise _err(ClusterError("not_in_cluster"))
    _not_on_standby(db)
    host = body.hostname.strip().lower().rstrip(".")
    if host and not re.fullmatch(r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}", host):
        raise _err(ClusterError("failover_bad_hostname"))
    if body.enabled and not host:
        raise _err(ClusterError("failover_no_hostname"))
    if host and host in {n.url.removeprefix("https://").split(":")[0] for n in db.execute(select(ClusterNode)).scalars()}:
        raise _err(ClusterError("failover_is_node_name"))
    set_setting(db, state.FAILOVER_KEY, {**body.model_dump(), "hostname": host})
    tracking.mark_changed(db)
    audit(db, request, admin, "cluster.failover", host, enabled=body.enabled)
    db.commit()
    return _overview(db)


@router.post("/promote")
def promote(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    """On a backup server: make it the primary (e.g. the old primary is gone for a while).
    The old primary becomes a backup as soon as it is back and syncs."""
    me = state.self_node(db)
    if me is None or me.role != "standby":
        raise _err(ClusterError("not_standby"))
    for n in db.execute(select(ClusterNode)).scalars():
        if n.role == "primary":
            n.role = "standby"
    me.role = "primary"
    set_setting(db, state.FAILOVER_STATE_KEY, None)
    tracking.mark_changed(db, allow_on_standby=True)
    audit(db, request, admin, "cluster.promote", me.name)
    db.commit()
    return _overview(db)


@router.post("/failover/take-over")
def failover_take_over(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    from ..cluster import failover

    try:
        failover.take_over_now(db, admin.email)
    except ClusterError as e:
        raise _err(e) from None
    audit(db, request, admin, "cluster.failover.take_over")
    db.commit()
    return _overview(db)


@router.post("/failover/point-primary")
def failover_point_primary(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    from ..cluster import failover

    try:
        failover.point_at_primary(db, admin.email)
    except ClusterError as e:
        raise _err(e) from None
    audit(db, request, admin, "cluster.failover.point_primary")
    db.commit()
    return _overview(db)


def _mx_plans(db) -> tuple[list[Any], list[dict[str, Any]], Any]:
    """MX records for every live domain: primary 10, backups 20 (all 10 when working together)."""
    from mailfilter_dns import publish
    from mailfilter_dns.cloudflare import Cloudflare

    from ..cluster.failover import make_cloudflare
    from ..models import DomainRoute, Domain
    from ..settings_store import get_secret

    token = get_secret(db, "cloudflare_token")
    if not token:
        raise _err(ClusterError("failover_no_cloudflare"), 409)
    cf: Cloudflare = make_cloudflare(token)
    nodes = list(db.execute(select(ClusterNode)).scalars())
    hosts = {n.url.removeprefix("https://").split(":")[0]: n for n in nodes}
    targets = [(20 if n.role == "standby" else 10, h) for h, n in sorted(hosts.items(), key=lambda x: (x[1].role == "standby", x[0]))]
    plans, skipped = [], []
    names = db.execute(select(Domain.name).join(DomainRoute, DomainRoute.domain_id == Domain.id).order_by(Domain.name)).scalars()
    for name in names:
        zone = cf.zone_for(name)
        if zone is None:
            skipped.append({"domain": name, "reason": "no_zone"})
            continue
        current = {r.content.lower().rstrip(".") for r in cf.records(zone, name=name, type="MX")}
        if not current & set(hosts):
            skipped.append({"domain": name, "reason": "not_live"})  # mail does not come through Mailfilter yet
            continue
        plan = publish.Plan(zone.id, zone.name, name, publish.plan_mx(cf, zone, name, targets))
        if plan.needed:
            plans.append(plan)
    return plans, skipped, cf


@router.post("/mx/plan")
def mx_plan(_: AdminUser, db: DB) -> dict[str, Any]:
    _not_on_standby(db)
    plans, skipped, _cf = _mx_plans(db)
    return {"plans": [p.to_dict() for p in plans], "skipped": skipped}


@router.post("/mx/apply")
def mx_apply(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    from mailfilter_dns import publish
    from mailfilter_dns.cloudflare import CloudflareError

    _not_on_standby(db)
    plans, skipped, cf = _mx_plans(db)
    done = []
    try:
        for p in plans:
            done += publish.apply(cf, p, actor=admin.email)
    except CloudflareError as e:
        raise _err(ClusterError("failover_cloudflare", str(e)[:300]), 502) from None
    audit(db, request, admin, "cluster.mx", "", domains=len(plans))
    db.commit()
    return {"domains": [p.domain for p in plans], "changes": len(done), "skipped": skipped}


# ---- other nodes -------------------------------------------------------------------
async def _raw(request: Request) -> bytes:
    if int(request.headers.get("content-length") or 0) > peer.MAX_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "cluster_too_large")
    raw = await request.body()
    if len(raw) > peer.MAX_BODY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "cluster_too_large")
    return raw


def _open(raw: bytes, db, op: str, *, member: bool = True) -> tuple[dict[str, Any], str, str]:
    """Check and open a sealed request. Returns (data, nonce, key)."""
    if not state.enabled(db):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cluster_not_in_cluster")
    key = state.secret(db)
    try:
        body = state.unseal(key, f"req:{op}", raw)
        state.remember_nonce(db, body["n"])
    except ClusterError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"cluster_{e.code}") from None
    data = body["d"]
    if member:
        sender = db.get(ClusterNode, str(data.get("from", "")))
        if sender is None or sender.is_self:
            db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cluster_unknown_node")
    return data, body["n"], key


def _answer(key: str, op: str, nonce: str, data: dict[str, Any]) -> Response:
    return Response(state.seal(key, f"resp:{op}", data, reply_to=nonce), media_type="application/octet-stream")


@public.post("/hello", include_in_schema=False)
async def peer_hello(request: Request, db: DB) -> Response:
    raw = await _raw(request)
    # Database work and applying a configuration block: keep them off the event loop.
    return await run_in_threadpool(_hello, raw, request, db)


def _hello(raw: bytes, request: Request, db) -> Response:
    data, nonce, key = _open(raw, db, "hello")
    n = db.get(ClusterNode, data["from"])
    n.last_seen_at = state.now()
    n.version = str(data.get("version", ""))[:40]
    n.revision = data.get("revision")
    if isinstance(data.get("info"), dict):
        n.info = data["info"]
    db.commit()
    me = state.self_node(db)
    return _answer(key, "hello", nonce, {"node": state.node_info(me), "revision": state.revision(db),
                                         "version": __version__, "info": peer.status_info(db)})


@public.post("/snapshot", include_in_schema=False)
async def peer_snapshot(request: Request, db: DB) -> Response:
    raw = await _raw(request)
    # Database work and applying a configuration block: keep them off the event loop.
    return await run_in_threadpool(_snapshot, raw, request, db)


def _snapshot(raw: bytes, request: Request, db) -> Response:
    _, nonce, key = _open(raw, db, "snapshot")
    db.commit()
    return _answer(key, "snapshot", nonce, {"snapshot": export(db)})


@public.post("/push", include_in_schema=False)
async def peer_push(request: Request, db: DB) -> Response:
    raw = await _raw(request)
    # Database work and applying a configuration block: keep them off the event loop.
    return await run_in_threadpool(_push, raw, request, db)


def _push(raw: bytes, request: Request, db) -> Response:
    data, nonce, key = _open(raw, db, "push")
    sender = db.get(ClusterNode, data["from"])
    sender_name = sender.name
    db.commit()
    snap = data.get("snapshot") or {}
    result = peer.receive_snapshot(db, snap, sender_name)
    sender = db.get(ClusterNode, data["from"])
    if sender is not None:
        sender.last_seen_at = state.now()
        sender.version = str(snap.get("version", ""))[:40]
        sender.revision = snap.get("revision")
        db.commit()
    last = get_setting(db, state.LAST_IMPORT_KEY) or {}
    return _answer(key, "push", nonce, {"result": result, "revision": state.revision(db),
                                        "error": last.get("error", "") if result == "failed" else ""})


@public.post("/join", include_in_schema=False)
async def peer_join(request: Request, db: DB) -> Response:
    raw = await _raw(request)
    # Database work and applying a configuration block: keep them off the event loop.
    return await run_in_threadpool(_join, raw, request, db)


def _join(raw: bytes, request: Request, db) -> Response:
    data, nonce, key = _open(raw, db, "join", member=False)
    info = data.get("node") or {}
    try:
        node_id = str(info["node_id"])
        name = _check_name(str(info["name"]))
        url = state.check_url(str(info["url"]))
    except (KeyError, ClusterError, HTTPException):
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cluster_bad_message") from None
    if node_id != data.get("from") or db.get(ClusterNode, node_id) is not None:
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cluster_bad_message")
    if any(n.url == url for n in state.peers(db)) or url == state.own_url():
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "cluster_url_in_use")
    if state.my_role(db) == "standby":
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "cluster_join_via_primary")
    db.add(ClusterNode(node_id=node_id, name=name, url=url, role="standby" if state.mode(db) == state.STANDBY else "member",
                       addresses=[str(a)[:45] for a in info.get("addresses", [])][:4],
                       version=str(data.get("version", ""))[:40], last_seen_at=state.now()))
    audit(db, request, None, "cluster.node_joined", name, url=url)
    db.commit()  # bumps the revision and pushes the new member list to the other nodes
    return _answer(key, "join", nonce, {"snapshot": export(db)})


@public.post("/leave", include_in_schema=False)
async def peer_leave(request: Request, db: DB) -> Response:
    raw = await _raw(request)
    # Database work and applying a configuration block: keep them off the event loop.
    return await run_in_threadpool(_leave, raw, request, db)


def _leave(raw: bytes, request: Request, db) -> Response:
    data, nonce, key = _open(raw, db, "leave", member=False)
    n = db.get(ClusterNode, str(data.get("node_id", "")))
    # A node may only take itself out.
    if n is not None and not n.is_self and data.get("from") == n.node_id and state.my_role(db) != "standby":
        audit(db, request, None, "cluster.node_left", n.name)
        db.delete(n)
    db.commit()
    return _answer(key, "leave", nonce, {"ok": True})
