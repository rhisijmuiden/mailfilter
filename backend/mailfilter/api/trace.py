"""Mail trace: find any message and see whether it was delivered, and if not, why."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Select, and_, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import TEXT

from ..deps import DB, CurrentUser, visible_domains
from .. import spamreport
from ..explain import short_reply, text
from ..models import Domain, QuarantineAction, TraceMessage, TraceRecipient, User
from ..settings import get_settings

router = APIRouter(prefix="/trace", tags=["trace"])

PROBLEM = ("bounced", "partly_delivered", "rejected", "deferred")
StatusFilter = Literal["all", "delivered", "problem", "quarantined", "deferred", "bounced", "rejected", "queued", "held"]


class Explanation(BaseModel):
    title: str
    action: str
    who: str


class RecipientOut(BaseModel):
    address: str
    status: str
    dsn: str | None = None
    reply: str | None = None
    relay_host: str | None = None
    relay_ip: str | None = None
    transport: str | None = None
    delay: float | None = None
    attempts: int = 0
    updated: datetime | None = None
    explanation: Explanation | None = None


class MessageOut(BaseModel):
    queue_id: str
    first_seen: datetime
    last_seen: datetime
    direction: str | None
    status: str
    sender: str | None
    client_host: str | None
    client_ip: str | None
    sasl_user: str | None
    message_id: str | None
    size: int | None
    spam_score: float | None
    spam_action: str | None
    is_bounce: bool
    recipients: list[RecipientOut]
    explanation: Explanation | None = None


class ListOut(BaseModel):
    items: list[MessageOut]
    total: int
    has_more: bool


class RouteNode(BaseModel):
    kind: Literal["source", "filter", "destination"]
    name: str | None
    ip: str | None = None
    detail: str | None = None
    status: str | None = None  # for destinations: sent / deferred / bounced …


class ActionOut(BaseModel):
    action: str
    learned: str | None
    user_email: str
    at: datetime


class DetailOut(MessageOut):
    symbols: list[dict]
    route: list[RouteNode]
    parent: str | None
    bounces: list[str]
    held: bool
    quarantine_actions: list[ActionOut]
    report: dict | None = None


class StatsOut(BaseModel):
    hours: int
    total: int
    inbound: int
    outbound: int
    by_status: dict[str, int]
    quarantined_now: int


def scope(stmt: Select, user: User) -> Select:
    """Customers only see messages to their own domains, and messages from their
    own domains that do not go to another domain on this Mailfilter. Anyone can
    put a customer's domain in the envelope sender, so a forged "from" alone must
    not show (or let them release) mail meant for another customer."""
    domains = visible_domains(user)
    if domains is None:
        return stmt
    lowered = [d.lower() for d in domains]
    rcpt_domain = func.lower(func.split_part(TraceRecipient.address, "@", 2))
    sender_domain = func.lower(func.split_part(TraceMessage.sender, "@", 2))
    rcpt_match = exists().where(TraceRecipient.queue_id == TraceMessage.queue_id, rcpt_domain.in_(lowered))
    to_other_customer = exists().where(
        TraceRecipient.queue_id == TraceMessage.queue_id,
        rcpt_domain.in_(select(func.lower(Domain.name)).where(func.lower(Domain.name).not_in(lowered)).scalar_subquery()),
    )
    return stmt.where(or_(rcpt_match, and_(sender_domain.in_(lowered), ~to_other_customer)))


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(stmt: Select, q: str) -> Select:
    q = q.strip()
    if not q:
        return stmt
    pat = _like(q.lower())
    return stmt.where(
        or_(
            func.lower(TraceMessage.sender).like(pat),
            exists().where(TraceRecipient.queue_id == TraceMessage.queue_id, func.lower(TraceRecipient.address).like(pat)),
            func.lower(TraceMessage.message_id).like(pat),
            TraceMessage.queue_id == q.upper(),
            func.lower(TraceMessage.client_host).like(pat),
            cast(TraceMessage.client_ip, TEXT).like(_like(q) if "/" not in q else q),
        )
    )


def _explain(rcpt: TraceRecipient, lang: str) -> Explanation | None:
    t = text(rcpt.explain, lang)
    return Explanation(**t) if t else None


def recipient_out(r: TraceRecipient, lang: str) -> RecipientOut:
    return RecipientOut(
        address=r.address, status=r.status, dsn=r.dsn, reply=short_reply(r.reply) or None,
        relay_host=r.relay_host, relay_ip=str(r.relay_ip) if r.relay_ip else None, transport=r.transport,
        delay=r.delay, attempts=r.attempts, updated=r.updated, explanation=_explain(r, lang),
    )


def visible_recipients(m: TraceMessage, user: User) -> list[TraceRecipient]:
    """A customer sees all recipients of mail they sent, but only their own
    recipients of mail they received (not other companies in Cc)."""
    domains = visible_domains(user)
    if domains is None:
        return m.recipients
    lowered = {d.lower() for d in domains}
    if m.sender and m.sender.rsplit("@", 1)[-1].lower() in lowered:
        return m.recipients
    return [r for r in m.recipients if r.address.rsplit("@", 1)[-1].lower() in lowered]


def message_out(m: TraceMessage, user: User, cls=MessageOut, **extra) -> MessageOut:
    lang = user.language
    rcpts = [recipient_out(r, lang) for r in visible_recipients(m, user)]
    first_problem = next((r.explanation for r in rcpts if r.status != "sent" and r.explanation), None)
    return cls(
        queue_id=m.queue_id, first_seen=m.first_seen, last_seen=m.last_seen, direction=m.direction, status=m.status,
        sender=m.sender, client_host=m.client_host, client_ip=str(m.client_ip) if m.client_ip else None,
        sasl_user=m.sasl_user, message_id=m.message_id, size=m.size, spam_score=m.spam_score,
        spam_action=m.spam_action, is_bounce=m.is_bounce, recipients=rcpts, explanation=first_problem, **extra,
    )


@router.get("", response_model=ListOut)
def list_messages(
    user: CurrentUser,
    db: DB,
    q: str = Query("", max_length=320),
    status_: StatusFilter = Query("all", alias="status"),
    direction: Literal["all", "inbound", "outbound"] = "all",
    hours: int = Query(24 * 7, ge=1, le=24 * 90),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
) -> ListOut:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(TraceMessage).where(TraceMessage.first_seen >= since)
    stmt = scope(search(stmt, q), user)
    if status_ == "problem":
        stmt = stmt.where(TraceMessage.status.in_(PROBLEM))
    elif status_ != "all":
        stmt = stmt.where(TraceMessage.status == status_)
    if direction != "all":
        stmt = stmt.where(TraceMessage.direction == direction)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.order_by(TraceMessage.first_seen.desc(), TraceMessage.queue_id).limit(limit).offset(offset)).scalars().all()
    return ListOut(items=[message_out(m, user) for m in rows], total=total, has_more=offset + len(rows) < total)


@router.get("/stats", response_model=StatsOut)
def stats(user: CurrentUser, db: DB, hours: int = Query(24, ge=1, le=24 * 90)) -> StatsOut:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = scope(select(TraceMessage.status, TraceMessage.direction).where(TraceMessage.first_seen >= since), user).subquery()
    rows = db.execute(select(base.c.status, base.c.direction, func.count()).group_by(base.c.status, base.c.direction)).all()
    by_status: dict[str, int] = {}
    inbound = outbound = 0
    for st, direction, n in rows:
        by_status[st] = by_status.get(st, 0) + n
        inbound += n if direction == "inbound" else 0
        outbound += n if direction == "outbound" else 0
    held = scope(select(func.count()).select_from(TraceMessage).where(
        and_(TraceMessage.status == "quarantined", TraceMessage.done.is_(False))), user)
    return StatsOut(
        hours=hours, total=sum(by_status.values()), inbound=inbound, outbound=outbound,
        by_status=by_status, quarantined_now=db.execute(held).scalar_one(),
    )


def get_visible(db, user: User, queue_id: str) -> TraceMessage:
    m = db.execute(scope(select(TraceMessage).where(TraceMessage.queue_id == queue_id), user)).scalar_one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return m


def route(m: TraceMessage, user: User) -> list[RouteNode]:
    s = get_settings()
    source = RouteNode(
        kind="source",
        name=m.client_host if m.client_host and m.client_host != "unknown" else None,
        ip=str(m.client_ip) if m.client_ip else None,
        detail=m.sasl_user or m.listener,
    )
    filt = RouteNode(
        kind="filter", name=s.hostname, ip=s.public_v4 or s.public_v6 or None,
        detail=None if m.spam_score is None else f"{m.spam_score:.1f}", status=m.spam_action,
    )
    dests: dict[str, RouteNode] = {}
    for r in visible_recipients(m, user):
        key = r.relay_host or ""
        if key not in dests:
            dests[key] = RouteNode(kind="destination", name=r.relay_host, ip=str(r.relay_ip) if r.relay_ip else None, status=r.status)
        elif r.status != "sent":
            dests[key].status = r.status
    return [source, filt, *dests.values()]


@router.get("/{queue_id}", response_model=DetailOut)
def detail(queue_id: str, user: CurrentUser, db: DB) -> DetailOut:
    m = get_visible(db, user, queue_id)
    bounces = list(db.execute(select(TraceMessage.queue_id).where(TraceMessage.parent == m.queue_id)).scalars())
    actions = db.execute(select(QuarantineAction).where(QuarantineAction.queue_id == m.queue_id).order_by(QuarantineAction.at)).scalars()
    symbols = sorted(
        ({"name": k, "score": (v or {}).get("score", 0) if isinstance(v, dict) else v} for k, v in (m.symbols or {}).items()),
        key=lambda s: -abs(float(s["score"] or 0)),
    )
    return message_out(
        m, user, DetailOut,
        symbols=symbols[:40], route=route(m, user), parent=m.parent, bounces=bounces,
        held=m.status == "quarantined" and not m.done,
        quarantine_actions=[ActionOut(action=a.action, learned=a.learned, user_email=a.user_email, at=a.at) for a in actions],
        report=spamreport.build(m.symbols, m.symbol_options, m.spam_score, m.spam_action, m.direction, user.language),
    )
