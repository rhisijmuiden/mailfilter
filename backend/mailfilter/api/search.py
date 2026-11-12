"""Quick search behind Ctrl+K: messages, domains and (for admins) users in one call.
Pages and settings are searched in the browser; this only returns data rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from ..deps import DB, CurrentUser, visible_domains
from ..models import Domain, TraceMessage, User
from .trace import _like, scope, search, visible_recipients

router = APIRouter(prefix="/search", tags=["search"])

DAYS = 30


class MessageHit(BaseModel):
    queue_id: str
    at: datetime
    sender: str | None
    recipients: list[str]
    status: str


class DomainHit(BaseModel):
    name: str


class UserHit(BaseModel):
    id: int
    email: str
    name: str
    role: str


class SearchOut(BaseModel):
    messages: list[MessageHit]
    domains: list[DomainHit]
    users: list[UserHit]


@router.get("", response_model=SearchOut)
def quick_search(user: CurrentUser, db: DB, q: str = Query("", max_length=320), limit: int = Query(5, ge=1, le=20)) -> SearchOut:
    q = q.strip()
    if len(q) < 2:
        return SearchOut(messages=[], domains=[], users=[])
    pat = _like(q.lower())

    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    stmt = scope(search(select(TraceMessage).where(TraceMessage.first_seen >= since), q), user)
    rows = db.execute(stmt.order_by(TraceMessage.first_seen.desc(), TraceMessage.queue_id).limit(limit)).scalars().all()
    messages = [
        MessageHit(queue_id=m.queue_id, at=m.first_seen, sender=m.sender,
                   recipients=[r.address for r in visible_recipients(m, user)], status=m.status)
        for m in rows
    ]

    allowed = visible_domains(user)
    dstmt = select(Domain.name).where(func.lower(Domain.name).like(pat))
    if allowed is not None:
        dstmt = dstmt.where(func.lower(Domain.name).in_([d.lower() for d in allowed]))
    domains = [DomainHit(name=n) for n in db.execute(dstmt.order_by(Domain.name).limit(limit)).scalars()]

    users: list[UserHit] = []
    if user.is_admin:
        ustmt = select(User).where(or_(func.lower(User.email).like(pat), func.lower(User.name).like(pat)))
        users = [UserHit(id=u.id, email=u.email, name=u.name, role=u.role)
                 for u in db.execute(ustmt.order_by(User.email).limit(limit)).scalars()]
    return SearchOut(messages=messages, domains=domains, users=users)
