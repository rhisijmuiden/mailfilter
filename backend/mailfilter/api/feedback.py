"""Spam reports from end users: the list, the settings, and the buttons to
learn a report by hand, undo it, or block the sender."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser, visible_domains
from ..feedback.ingest import DEFAULTS, KEEP_RAW_DAYS, SETTING, addresses, block_sender, options, rspamd_learn
from ..models import SenderListEntry, SpamFeedback, User
from ..settings_store import set_setting
from .lists import save_maps

router = APIRouter(prefix="/spam-reports", tags=["spam-reports"])

Kind = Literal["spam", "ham"]
LEARNABLE = ("unverified", "not_found", "not_learned", "undone")


class ReportOut(BaseModel):
    id: int
    at: datetime
    kind: Kind
    status: str
    detail: str
    source: str
    reporter: str
    verified: str
    queue_id: str | None
    sender: str
    recipient: str
    subject: str
    score: float | None
    action: str
    learned: str | None
    blocked: bool
    handled_by: str
    can_learn: bool
    can_undo: bool
    can_block: bool


class Stats(BaseModel):
    spam: int
    ham: int
    learned: int
    waiting: int  # could be learned by hand
    refused: int


class ListOut(BaseModel):
    items: list[ReportOut]
    total: int
    stats: Stats


class OptionsModel(BaseModel):
    enabled: bool = DEFAULTS["enabled"]
    require_auth: bool = DEFAULTS["require_auth"]
    auto_block: bool = DEFAULTS["auto_block"]
    per_hour: int = Field(DEFAULTS["per_hour"], ge=0, le=10000)


class SettingsOut(BaseModel):
    options: OptionsModel
    spam_address: str
    ham_address: str
    keep_days: int


def _scoped(stmt, user: User):
    domains = visible_domains(user)
    if domains is None:
        return stmt
    names = [d.lower() for d in domains]
    rcpt_dom = func.lower(func.split_part(SpamFeedback.recipient, "@", 2))
    return stmt.where(or_(SpamFeedback.domain.in_(names), rcpt_dom.in_(names)))


def _has_raw(db: Session, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return set(db.execute(select(SpamFeedback.id).where(SpamFeedback.id.in_(ids), SpamFeedback.raw.is_not(None))).scalars())


def _out(f: SpamFeedback, has_raw: bool) -> ReportOut:
    return ReportOut(
        id=f.id, at=f.at, kind=f.kind, status=f.status, detail=f.detail, source=f.source, reporter=f.reporter,
        verified=f.verified, queue_id=f.queue_id, sender=f.sender, recipient=f.recipient, subject=f.subject,
        score=f.score, action=f.action, learned=f.learned, blocked=f.blocked, handled_by=f.handled_by,
        can_learn=has_raw and f.status in LEARNABLE,
        can_undo=has_raw and f.status == "learned",
        can_block=f.kind == "spam" and not f.blocked and bool(f.sender) and bool(f.recipient),
    )


@router.get("", response_model=ListOut)
def list_reports(user: CurrentUser, db: DB, kind: Kind | None = None, status_: str | None = Query(None, alias="status"),
                 days: int = Query(30, ge=1, le=400), q: str = Query("", max_length=320)) -> ListOut:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = _scoped(select(SpamFeedback).where(SpamFeedback.at >= since), user)
    counts = db.execute(_scoped(select(SpamFeedback.kind, SpamFeedback.status, func.count())
                                .where(SpamFeedback.at >= since), user).group_by(SpamFeedback.kind, SpamFeedback.status)).all()
    stats = Stats(spam=0, ham=0, learned=0, waiting=0, refused=0)
    for k, s, n in counts:
        setattr(stats, k, getattr(stats, k) + n)
        if s == "learned":
            stats.learned += n
        elif s in ("unverified", "not_found", "not_learned"):
            stats.waiting += n
        elif s in ("not_customer", "rate_limited", "no_message", "disabled"):
            stats.refused += n
    stmt = base
    if kind:
        stmt = stmt.where(SpamFeedback.kind == kind)
    if status_:
        stmt = stmt.where(SpamFeedback.status == status_)
    if q.strip():
        pat = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(SpamFeedback.reporter.like(pat), func.lower(SpamFeedback.sender).like(pat),
                              func.lower(SpamFeedback.subject).like(pat), SpamFeedback.recipient.like(pat),
                              SpamFeedback.queue_id == q.strip()))
    rows = db.execute(stmt.order_by(SpamFeedback.at.desc(), SpamFeedback.id.desc()).limit(1000)).scalars().all()
    raw = _has_raw(db, [r.id for r in rows])
    return ListOut(items=[_out(r, r.id in raw) for r in rows], total=len(rows), stats=stats)


@router.get("/settings", response_model=SettingsOut)
def get_settings_(user: CurrentUser, db: DB) -> SettingsOut:
    a = addresses()
    return SettingsOut(options=OptionsModel(**options(db)), spam_address=a["spam"], ham_address=a["ham"], keep_days=KEEP_RAW_DAYS)


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: OptionsModel, request: Request, user: AdminUser, db: DB) -> SettingsOut:
    set_setting(db, SETTING, body.model_dump())
    audit(db, request, user, "spam_reports_settings", "", **body.model_dump())
    db.commit()
    return get_settings_(user, db)


def _get(db: Session, user: User, report_id: int) -> SpamFeedback:
    f = db.execute(_scoped(select(SpamFeedback), user).where(SpamFeedback.id == report_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return f


def _raw(db: Session, f: SpamFeedback) -> bytes:
    raw = db.execute(select(SpamFeedback.raw).where(SpamFeedback.id == f.id)).scalar_one_or_none()
    if not raw:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_copy")
    return raw


def _done(db: Session, f: SpamFeedback) -> ReportOut:
    db.commit()
    db.refresh(f)
    return _out(f, bool(_has_raw(db, [f.id])))


@router.post("/{report_id}/learn", response_model=ReportOut)
def learn_now(report_id: int, request: Request, user: CurrentUser, db: DB) -> ReportOut:
    f = _get(db, user, report_id)
    if f.status not in LEARNABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_learnable")
    if not rspamd_learn(f.kind, _raw(db, f)):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "rspamd_refused")
    f.status, f.learned, f.handled_by, f.detail = "learned", f.kind, user.email, ""
    audit(db, request, user, "spam_report_learn", str(f.id), domain=f.domain, kind=f.kind, sender=f.sender)
    return _done(db, f)


@router.post("/{report_id}/undo", response_model=ReportOut)
def undo(report_id: int, request: Request, user: CurrentUser, db: DB) -> ReportOut:
    """Learn the opposite (Rspamd moves the message to the other class) and
    take the sender off the block list again if the report put it there."""
    f = _get(db, user, report_id)
    if f.status != "learned":
        raise HTTPException(status.HTTP_409_CONFLICT, "not_learned")
    other = "ham" if f.kind == "spam" else "spam"
    if not rspamd_learn(other, _raw(db, f)):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "rspamd_refused")
    f.status, f.learned, f.handled_by = "undone", other, user.email
    changed = False
    if f.blocked:
        e = db.execute(select(SenderListEntry).where(SenderListEntry.recipient == (f.recipient or f.reporter).lower(),
                                                     SenderListEntry.sender == f.sender.lower(),
                                                     SenderListEntry.list == "block")).scalar_one_or_none()
        if e is not None:
            db.delete(e)
            changed = True
        f.blocked = False
    audit(db, request, user, "spam_report_undo", str(f.id), domain=f.domain, kind=f.kind, sender=f.sender)
    if changed:
        save_maps(db)
    return _done(db, f)


@router.post("/{report_id}/block", response_model=ReportOut)
def block(report_id: int, request: Request, user: CurrentUser, db: DB) -> ReportOut:
    f = _get(db, user, report_id)
    if f.kind != "spam" or not f.sender or not f.recipient:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing_to_block")
    domains = visible_domains(user)
    if domains is not None and f.recipient.rsplit("@", 1)[-1] not in {d.lower() for d in domains}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_domain")
    if not block_sender(db, f, by=user.email):
        raise HTTPException(422, "bad_sender")
    audit(db, request, user, "list_block_add", f"{f.recipient} <- {f.sender}", domain=f.recipient, report=f.id)
    save_maps(db)
    db.refresh(f)
    return _out(f, bool(_has_raw(db, [f.id])))
