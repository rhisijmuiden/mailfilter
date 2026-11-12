"""Audit log viewer and the two-factor policy.

Admins see every entry. Customers see their own actions plus the entries about
their own domains, without the IP address of other people's actions.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select

from .. import twofactor
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..models import AuditLog, User

router = APIRouter(tags=["audit"])

MAX_EXPORT = 50_000


class AuditEntry(BaseModel):
    id: int
    at: datetime
    user_id: int | None
    user_email: str | None
    user_name: str | None
    action: str
    target: str
    domain: str | None
    detail: dict[str, Any]
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    page: int
    page_size: int


class AuditFilters(BaseModel):
    actions: list[str]
    users: list[dict[str, Any]]
    domains: list[str]


class SecurityPolicy(BaseModel):
    require_2fa: Literal["off", "admins", "all"]


def _scope(stmt, user: User):
    if user.is_admin:
        return stmt
    domains = [d.lower() for d in user.domain_names]
    cond = AuditLog.user_id == user.id
    if domains:
        cond = or_(cond, AuditLog.domain.in_(domains))
    return stmt.where(cond)


def _filtered(user: User, q: str | None, action: str | None, user_id: int | None, domain: str | None,
              since: datetime | None, until: datetime | None):
    stmt = _scope(select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.user_id), user)
    if action:
        # Comma-separated. "dns" also matches "dns.publish.spf", "quarantine" also
        # matches "quarantine_released".
        conds = []
        for a in {x.strip() for x in action.split(",") if x.strip()}:
            esc = a.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds += [AuditLog.action == a, AuditLog.action.like(f"{esc}.%"), AuditLog.action.like(f"{esc}\\_%")]
        stmt = stmt.where(or_(*conds))
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if domain:
        stmt = stmt.where(AuditLog.domain == domain.lower())
    if since:
        stmt = stmt.where(AuditLog.at >= since)
    if until:
        stmt = stmt.where(AuditLog.at < until)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            AuditLog.target.ilike(like), AuditLog.user_email.ilike(like), AuditLog.action.ilike(like),
            AuditLog.domain.ilike(like), cast(AuditLog.ip, String).ilike(like), cast(AuditLog.detail, String).ilike(like),
        ))
    return stmt


def _entry(row: AuditLog, name: str | None, viewer: User) -> AuditEntry:
    own = viewer.is_admin or row.user_id == viewer.id
    return AuditEntry(
        id=row.id, at=row.at, user_id=row.user_id, user_email=row.user_email, user_name=name,
        action=row.action, target=row.target, domain=row.domain, detail=row.detail or {},
        ip=str(row.ip) if row.ip and own else None,
    )


@router.get("/audit", response_model=AuditPage)
def list_audit(
    user: CurrentUser, db: DB,
    q: str | None = Query(default=None, max_length=200),
    action: str | None = Query(default=None, max_length=2000),
    user_id: int | None = None,
    domain: str | None = Query(default=None, max_length=253),
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> AuditPage:
    stmt = _filtered(user, q, action, user_id, domain, since, until)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return AuditPage(items=[_entry(r, n, user) for r, n in rows], total=total, page=page, page_size=page_size)


@router.get("/audit/filters", response_model=AuditFilters)
def audit_filters(user: CurrentUser, db: DB) -> AuditFilters:
    base = _scope(select(AuditLog), user).subquery()
    actions = sorted(db.execute(select(base.c.action).distinct()).scalars())
    domains = sorted(d for d in db.execute(select(base.c.domain).distinct()).scalars() if d)
    users = db.execute(
        select(base.c.user_id, func.max(base.c.user_email)).where(base.c.user_id.is_not(None)).group_by(base.c.user_id)
    ).all()
    return AuditFilters(
        actions=actions, domains=domains,
        users=sorted(({"id": i, "email": e} for i, e in users), key=lambda u: u["email"] or ""),
    )


@router.get("/audit/export.csv")
def export_audit(
    user: CurrentUser, db: DB,
    q: str | None = Query(default=None, max_length=200),
    action: str | None = Query(default=None, max_length=2000),
    user_id: int | None = None,
    domain: str | None = Query(default=None, max_length=253),
    since: datetime | None = None,
    until: datetime | None = None,
) -> StreamingResponse:
    stmt = _filtered(user, q, action, user_id, domain, since, until).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(MAX_EXPORT)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "user", "action", "target", "domain", "ip", "detail"])
    for row, name in db.execute(stmt):
        e = _entry(row, name, user)
        w.writerow([e.at.isoformat(), e.user_email or "", e.action, e.target, e.domain or "", e.ip or "",
                    "; ".join(f"{k}={v}" for k, v in e.detail.items())])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="mailfilter-audit.csv"'})


@router.get("/system/security", response_model=SecurityPolicy)
def get_security(_: AdminUser, db: DB) -> SecurityPolicy:
    return SecurityPolicy(require_2fa=twofactor.get_policy(db))


@router.put("/system/security", response_model=SecurityPolicy)
def put_security(body: SecurityPolicy, request: Request, admin: AdminUser, db: DB) -> SecurityPolicy:
    old = twofactor.get_policy(db)
    if body.require_2fa != "off" and not admin.two_factor_enabled:
        # Otherwise the admin locks themselves out of everything but the setup.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "enable_two_factor_first")
    twofactor.set_policy(db, body.require_2fa)
    audit(db, request, admin, "security.require_2fa", body.require_2fa, previous=old)
    db.commit()
    return SecurityPolicy(require_2fa=body.require_2fa)
