"""DKIM keys page: key age, automatic rotation settings, "rotate now" (admin only).

The work itself is in ``mailfilter.dkim_rotation``; the timer
``mailfilter-dkim-rotate`` moves rotations forward every few hours, and so
does "Check now" here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import dkim_rotation as rot
from ..audit import audit
from ..deps import DB, AdminUser
from ..settings_store import set_setting

router = APIRouter(prefix="/dkim", tags=["dkim"])


class SettingsIn(BaseModel):
    enabled: bool
    interval_months: int = Field(ge=1, le=24)
    overlap_days: int = Field(ge=2, le=60)
    publish_wait_hours: int = Field(ge=1, le=336)
    manual_dns: bool


def _domain(db, name: str) -> str:
    name = name.strip().lower().rstrip(".")
    if name not in {n for n, _ in rot.domains(db)}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown_domain")
    return name


def _leader_only(db) -> None:
    if not rot.is_leader(db):
        raise HTTPException(status.HTTP_409_CONFLICT, f"not_leader:{rot.leader(db)}")


@router.get("")
def overview(db: DB, _: AdminUser) -> dict:
    return rot.overview(db)


@router.put("/settings")
def save_settings(body: SettingsIn, db: DB, user: AdminUser, request: Request) -> dict:
    before = rot.config(db)
    set_setting(db, rot.CONFIG_KEY, body.model_dump())
    audit(db, request, user, "dkim_rotation_settings", "dkim",
          **{k: v for k, v in body.model_dump().items() if before.get(k) != v})
    db.commit()
    return rot.config(db)


@router.post("/run")
def run_now(db: DB, user: AdminUser) -> dict:
    _leader_only(db)
    res = rot.run(db)
    db.info.pop("dkim_switched", None)
    db.commit()
    return {"ran": res.ran, "reason": res.reason, "events": res.events, "errors": res.errors}


@router.post("/{domain}/rotate")
def rotate(domain: str, db: DB, user: AdminUser, request: Request) -> dict:
    _leader_only(db)
    name = _domain(db, domain)
    try:
        rot.start(db, name, user=user, request=request)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"helper:{e}") from e
    db.info.pop("dkim_switched", None)
    db.commit()
    return rot.overview(db)


@router.post("/{domain}/cancel")
def cancel(domain: str, db: DB, user: AdminUser, request: Request) -> dict:
    _leader_only(db)
    name = _domain(db, domain)
    try:
        rot.cancel(db, name, user=user, request=request)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.commit()
    return rot.overview(db)
