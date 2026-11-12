"""Paused senders (automatic protection against hacked websites and mailboxes).

Admins see every paused sender, pause one by hand and release it with one
click. Customers see the pauses of their own relay IPs and accounts (read
only: a hacked customer website should not be able to unpause itself).
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from .. import abuse
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser, visible_domains
from ..gateway import ensure_imported
from ..models import OutboundHold, RelayNetwork, User

router = APIRouter(prefix="/gateway", tags=["gateway"])


class HoldOut(BaseModel):
    id: int
    kind: Literal["ip", "account"]
    value: str
    reason: str
    detail: dict
    automatic: bool
    created_at: datetime
    released_at: datetime | None = None
    released_by: str | None = None
    held_messages: int = 0


class PauseIn(BaseModel):
    kind: Literal["ip", "account"]
    value: str = Field(min_length=1, max_length=320)


class ReleaseIn(BaseModel):
    held_mail: Literal["deliver", "delete", "keep"] = "deliver"


class AbuseSettingsIO(BaseModel):
    enabled: bool = True
    window_minutes: int = Field(15, ge=5, le=240)
    spam_min: int = Field(10, ge=1, le=100_000)
    spam_percent: int = Field(30, ge=1, le=100)
    volume_min: int = Field(300, ge=10, le=10_000_000)
    volume_factor: int = Field(10, ge=2, le=1000)
    quiet_hours: int = Field(6, ge=0, le=168)
    exempt: list[str] = Field(default_factory=list, max_length=500)
    notify: list[str] = Field(default_factory=list, max_length=50)


# ---- who may see which pause ---------------------------------------------------------
def _customer_owns(db, user: User, h: OutboundHold) -> bool:
    scope = visible_domains(user)
    if scope is None:
        return True
    if h.kind == "account":
        return h.value.rsplit("@", 1)[-1] in {d.lower() for d in scope}
    try:
        ip = ipaddress.ip_address(h.value)
    except ValueError:
        return False
    for n in db.execute(select(RelayNetwork).where(RelayNetwork.domain_id.is_not(None))).scalars():
        try:
            if n.domain.name in scope and ip in ipaddress.ip_network(n.cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _out(db, h: OutboundHold) -> HoldOut:
    by = db.get(User, h.released_by) if h.released_by else None
    return HoldOut(id=h.id, kind=h.kind, value=h.value, reason=h.reason, detail=h.detail or {}, automatic=h.automatic,
                   created_at=h.created_at, released_at=h.released_at, released_by=by.email if by else None,
                   held_messages=0 if h.released_at else len(abuse.held_messages(db, h)))


def _get(db, user: User, hold_id: int) -> OutboundHold:
    h = db.get(OutboundHold, hold_id)
    if h is None or not _customer_owns(db, user, h):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return h


# ---- endpoints --------------------------------------------------------------------------
@router.get("/holds", response_model=list[HoldOut])
def list_holds(user: CurrentUser, db: DB) -> list[HoldOut]:
    """Active pauses first, then the ones released in the last 7 days."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = db.execute(select(OutboundHold).where(
        or_(OutboundHold.released_at.is_(None), OutboundHold.released_at >= since))
        .order_by(OutboundHold.released_at.is_not(None), OutboundHold.created_at.desc())).scalars()
    return [_out(db, h) for h in rows if _customer_owns(db, user, h)]


@router.post("/holds", response_model=HoldOut, status_code=201)
def pause_sender(body: PauseIn, request: Request, admin: AdminUser, db: DB) -> HoldOut:
    ensure_imported(db)
    value = body.value.strip().lower()
    if body.kind == "ip":
        try:
            value = str(ipaddress.ip_address(value))
        except ValueError:
            raise HTTPException(422, "invalid_ip")
    return _out(db, abuse.pause(db, admin, body.kind, value, request))


@router.post("/holds/{hold_id}/release")
def release_sender(hold_id: int, body: ReleaseIn, request: Request, admin: AdminUser, db: DB) -> dict:
    h = _get(db, admin, hold_id)
    if h.released_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already_released")
    counts = abuse.release(db, h, admin, body.held_mail, request)
    return {**counts, "hold": _out(db, h).model_dump(mode="json")}


@router.get("/abuse-settings", response_model=AbuseSettingsIO)
def get_abuse_settings(_: AdminUser, db: DB) -> AbuseSettingsIO:
    return AbuseSettingsIO(**abuse.asdict(abuse.load_settings(db)))


@router.put("/abuse-settings", response_model=AbuseSettingsIO)
def put_abuse_settings(body: AbuseSettingsIO, request: Request, admin: AdminUser, db: DB) -> AbuseSettingsIO:
    data = body.model_dump()
    data["exempt"] = sorted({e.strip().lower() for e in data["exempt"] if e.strip()})
    data["notify"] = sorted({e.strip() for e in data["notify"] if e.strip()})
    abuse.save_settings(db, abuse.AbuseSettings(**data))
    audit(db, request, admin, "abuse_settings_updated", "", enabled=data["enabled"])
    db.commit()
    return AbuseSettingsIO(**data)
