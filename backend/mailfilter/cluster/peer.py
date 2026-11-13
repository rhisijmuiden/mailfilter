"""Talking to the other nodes: the client side of join, leave, hello, push and sync.

The server side lives in api/cluster.py (/api/cluster/peer/<op>). Every node
is equal: a change made on any node is pushed to the others straight away, and
a timer (mailfilter-cluster.timer, every minute) compares revisions with every
peer and fetches or pushes what is missing. When a peer is down, its node keeps
filtering mail with the configuration it has and catches up when it is back.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import __version__
from ..db import session as new_session
from ..models import ClusterNode
from . import state
from .snapshot import apply_snapshot, export
from .state import ClusterError

MAX_BODY = 50_000_000


def http_transport(url: str, op: str, token: str) -> tuple[int, str]:
    try:
        r = httpx.post(f"{url}/api/cluster/peer/{op}", content=token.encode(), timeout=30,
                       headers={"X-Requested-With": "mailfilter", "Content-Type": "application/octet-stream",
                                "User-Agent": f"Mailfilter-cluster/{__version__}"})
    except httpx.HTTPError as e:
        raise ClusterError("unreachable", str(e)[:300]) from None
    return r.status_code, r.text


# Tests replace this with a function that calls another (fake) node.
TRANSPORT: Callable[[str, str, str], tuple[int, str]] = http_transport


def _error_code(status: int, text: str) -> ClusterError:
    try:
        detail = json.loads(text).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        return ClusterError(str(detail.get("code", f"http_{status}")).removeprefix("cluster_"), str(detail.get("detail", "")))
    if isinstance(detail, str):
        return ClusterError(detail.removeprefix("cluster_"))
    return ClusterError("unreachable" if status >= 500 else f"http_{status}", text[:200])


def call_url(url: str, key: str, op: str, data: dict[str, Any], sender: str) -> dict[str, Any]:
    nonce = secrets.token_hex(16)
    token = state.seal(key, f"req:{op}", {**data, "from": sender}, nonce=nonce)
    status, text = TRANSPORT(url, op, token)
    if status != 200:
        raise _error_code(status, text)
    return state.unseal(key, f"resp:{op}", text, reply_to=nonce)["d"]


def call(db: Session, node: ClusterNode, op: str, data: dict[str, Any]) -> dict[str, Any]:
    cfg = state.config(db) or {}
    try:
        return call_url(node.url, state.secret(db), op, data, cfg.get("node_id", ""))
    except ClusterError as e:
        prev = state.previous_secret(db) if e.code == "auth_failed" else None
        if not prev:
            raise
        # The peer missed a secret change (a node was removed): reach it with the
        # old secret; the snapshot we push then gives it the new one.
        return call_url(node.url, prev, op, data, cfg.get("node_id", ""))


def _hello_data(db: Session) -> dict[str, Any]:
    me = state.self_node(db)
    return {"node": state.node_info(me) if me else None, "revision": state.revision(db), "version": __version__,
            "info": status_info(db)}


def status_info(db: Session) -> dict[str, Any]:
    """What this node tells the others about itself (shown on their Cluster page)."""
    from .failover import current_state

    return {"failover": current_state(db)}


def _seen(node: ClusterNode, info: dict[str, Any], started: float) -> None:
    node.last_seen_at = state.now()
    node.version = str(info.get("version", ""))[:40]
    node.revision = info.get("revision")
    node.latency_ms = int((time.monotonic() - started) * 1000)
    if isinstance(info.get("info"), dict):
        node.info = info["info"]


def _failed(node: ClusterNode, e: ClusterError) -> None:
    node.last_error = e.code + (f": {e.detail}" if e.detail else "")
    node.last_error_at = state.now()


def record_import(db: Session, ok: bool, source: str, error: ClusterError | None = None) -> None:
    from ..settings_store import set_setting

    set_setting(db, state.LAST_IMPORT_KEY, {
        "at": state.now().isoformat(), "ok": ok, "from": source,
        "error": (error.code if error else ""), "detail": (error.detail if error else "")[:2000],
    })


def receive_snapshot(db: Session, snap: dict[str, Any], source: str) -> str:
    """Apply a snapshot from a peer if it is newer. Commits. Returns
    "applied", "same", "older", "removed" or "failed"."""
    # One import at a time (a push and the timer can arrive together).
    db.execute(text("SELECT pg_advisory_xact_lock(7212001)"))
    mine = state.revision(db)
    theirs = snap.get("revision") if isinstance(snap, dict) else None
    if not state.newer(theirs, mine):
        return "same" if state.rev_key(theirs) == state.rev_key(mine) else "older"
    try:
        result = apply_snapshot(db, snap)
    except ClusterError as e:
        db.rollback()
        record_import(db, False, source, e)
        db.commit()
        return "failed"
    if result == "applied":
        record_import(db, True, source)
    db.commit()
    return result


def sync_peer(db: Session, node: ClusterNode) -> str:
    """Compare revisions with one peer and fetch or push. Commits."""
    started = time.monotonic()
    node_id = node.node_id
    try:
        info = call(db, node, "hello", _hello_data(db))
        _seen(node, info, started)
        db.commit()
        mine = state.revision(db)
        if state.newer(info.get("revision"), mine):
            snap = call(db, node, "snapshot", {})["snapshot"]
            result = receive_snapshot(db, snap, node.name)
        elif state.newer(mine, info.get("revision")):
            result = call(db, node, "push", {"snapshot": export(db)}).get("result", "")
        else:
            result = "same"
        node = db.get(ClusterNode, node_id)
        if node is not None and result in ("applied", "same", "older"):
            node.last_sync_at = state.now()
            node.last_error = ""
            db.commit()
        return result
    except ClusterError as e:
        db.rollback()
        node = db.get(ClusterNode, node_id)
        if node is not None:
            _failed(node, e)
            db.commit()
        return "error"


def sync_all(db: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    if not state.enabled(db):
        return out
    for node in state.peers(db):
        out[node.name] = sync_peer(db, node)
        if not state.enabled(db):  # this node was removed from the cluster
            break
    return out


def push_all() -> None:
    """Push this node's configuration to every peer (after a change here)."""
    with new_session() as db:
        if not state.enabled(db):
            return
        snap = None
        for node in state.peers(db):
            started = time.monotonic()
            try:
                snap = snap or export(db)
                res = call(db, node, "push", {"snapshot": snap})
                node.latency_ms = int((time.monotonic() - started) * 1000)
                node.last_seen_at = state.now()
                node.revision = res.get("revision")
                if res.get("result") in ("applied", "same", "older"):
                    node.last_sync_at = state.now()
                    node.last_error = ""
                else:
                    _failed(node, ClusterError("peer_" + str(res.get("result")), str(res.get("error", ""))))
            except ClusterError as e:
                _failed(node, e)
            db.commit()


# ---- membership -------------------------------------------------------------------
def join(db: Session, code: str, name: str) -> dict[str, Any]:
    """Join the cluster of a join code. This node's configuration, users and DKIM
    keys are replaced by the cluster's. Commits."""
    from ..settings_store import set_setting

    if state.enabled(db):
        raise ClusterError("already_in_cluster")
    jc = state.parse_join_code(code)
    url = state.own_url()
    if not url:
        raise ClusterError("no_hostname")
    if url == jc["url"]:
        raise ClusterError("join_self")
    db.info["cluster_import"] = True  # the cluster's revision is adopted as it is
    node_id = state.new_id()
    me = {"node_id": node_id, "name": name, "url": url, "addresses": state.own_addresses()}
    answer = call_url(jc["url"], jc["secret"], "join", {"node": me, "version": __version__}, node_id)
    snap = answer.get("snapshot") or {}
    cl = snap.get("cluster") or {}
    if cl.get("cluster_id") != jc["cluster_id"]:
        raise ClusterError("other_cluster")
    set_setting(db, state.CONFIG_KEY, {"cluster_id": jc["cluster_id"], "node_id": node_id,
                                       "spf_name": cl.get("spf_name", ""), "created_at": state.now().isoformat()})
    state.set_cluster_secret(db, cl.get("secret") or jc["secret"])
    db.add(ClusterNode(node_id=node_id, is_self=True, **{k: me[k] for k in ("name", "url", "addresses")}))
    db.flush()
    try:
        apply_snapshot(db, snap, joining=True)
    except Exception:
        db.rollback()
        try:  # undo our membership on the other side
            call_url(jc["url"], cl.get("secret") or jc["secret"], "leave", {"node_id": node_id}, node_id)
        except ClusterError:
            pass
        raise
    for n in state.peers(db):
        if n.url == jc["url"]:  # it just answered
            n.last_seen_at = n.last_sync_at = state.now()
            n.version = str(snap.get("version", ""))[:40]
            n.revision = snap.get("revision")
    record_import(db, True, jc.get("name") or jc["url"])
    db.commit()
    return snap


def leave(db: Session) -> None:
    """Leave the cluster; this server keeps its configuration and runs on its own. Commits."""
    cfg = state.config(db)
    if not cfg:
        raise ClusterError("not_in_cluster")
    for node in state.peers(db):
        try:
            call(db, node, "leave", {"node_id": cfg["node_id"]})
        except ClusterError:
            pass
    state.leave_locally(db)
    db.commit()
