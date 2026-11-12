"""Notice configuration changes so the other nodes get them.

A SQLAlchemy session hook: when a transaction changes a synced table or
setting, the cluster revision is bumped in that same transaction and, after
the commit, the change is pushed to the other nodes in the background. On a
server that is not in a cluster the hook only sets a flag and does nothing.
"""

from __future__ import annotations

import os
import threading

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from ..models import AppSetting
from . import state
from .snapshot import LOCAL_COLUMNS, SYNCED_MODELS, SYNCED_SETTINGS

FLAG = "cluster_changed"
PUSH = "cluster_push"


def _changes_config(obj) -> bool:
    if isinstance(obj, AppSetting):
        return obj.key in SYNCED_SETTINGS
    if not isinstance(obj, SYNCED_MODELS):
        return False
    local = LOCAL_COLUMNS.get(type(obj), frozenset())
    ins = inspect(obj)
    return any(a.history.has_changes() for a in ins.attrs if a.key not in local)


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _ctx, _instances) -> None:
    if session.info.get("cluster_import") or session.info.get(FLAG):
        return
    for obj in session.new | session.deleted:
        if isinstance(obj, SYNCED_MODELS) or (isinstance(obj, AppSetting) and obj.key in SYNCED_SETTINGS):
            session.info[FLAG] = True
            return
    if any(_changes_config(o) for o in session.dirty):
        session.info[FLAG] = True


@event.listens_for(Session, "before_commit")
def _before_commit(session: Session) -> None:
    if session.info.get("cluster_import"):
        session.info.pop(FLAG, None)
        return
    session.flush()
    if session.info.pop(FLAG, None) and state.enabled(session):
        state.bump_revision(session)
        session.flush()
        session.info[PUSH] = True


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    session.info.pop("cluster_import", None)
    if session.info.pop(PUSH, None):
        schedule_push()


@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(session: Session, _previous) -> None:
    if not session.in_transaction():
        for k in (FLAG, PUSH, "cluster_import"):
            session.info.pop(k, None)


# ---- background push --------------------------------------------------------------
_lock = threading.Lock()
_running = False
_again = False


def schedule_push() -> None:
    """Push the new configuration to the peers now (the timer catches up anyway)."""
    global _running, _again
    if os.environ.get("MF_CLUSTER_PUSH", "1") == "0":
        return
    with _lock:
        if _running:
            _again = True
            return
        _running = True
    threading.Thread(target=_push_loop, name="cluster-push", daemon=True).start()


def _push_loop() -> None:
    global _running, _again
    from .peer import push_all

    while True:
        try:
            push_all()
        except Exception:  # noqa: BLE001 - never break the web request that triggered it
            pass
        with _lock:
            if not _again:
                _running = False
                return
            _again = False


def install() -> None:
    """The hooks above are registered when this module is imported; callers use
    this to make that import explicit."""
