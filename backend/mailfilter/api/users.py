"""Admin: manage users. Customers only see mail for the domains linked to them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import twofactor
from ..audit import audit
from ..deps import DB, AdminUser
from ..gateway import apply_changes, customer_sender_domains, ensure_imported
from ..models import AuthSession, Domain, RelayNetwork, User
from ..security import MIN_PASSWORD_LENGTH, generate_password, hash_password

router = APIRouter(prefix="/users", tags=["users"])


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    language: str
    is_active: bool
    domains: list[str]
    last_login_at: datetime | None
    two_factor_enabled: bool = False


class UserIn(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(default="", max_length=200)
    role: Literal["admin", "customer"] = "customer"
    language: Literal["en", "nl"] = "en"
    is_active: bool = True
    domains: list[str] = []
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=500)


class UserCreated(UserOut):
    password: str | None = None


def out(u: User) -> UserOut:
    return UserOut(
        id=u.id, email=u.email, name=u.name, role=u.role, language=u.language,
        is_active=u.is_active, domains=u.domain_names, last_login_at=u.last_login_at,
        two_factor_enabled=u.two_factor_enabled,
    )


def _domains(db, names: list[str]) -> list[Domain]:
    wanted = {n.strip().lower() for n in names if n.strip()}
    found = list(db.execute(select(Domain).where(func.lower(Domain.name).in_(wanted))).scalars())
    missing = wanted - {d.name.lower() for d in found}
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown_domain:{','.join(sorted(missing))}")
    return found


def _relock(db, before: dict[str, list[str]]) -> None:
    """Relay networks of customer domains may only send as that customer's
    domains: when the customer/domain links change, write the gateway again."""
    after = customer_sender_domains(db)
    changed = {d for d in set(before) | set(after) if before.get(d) != after.get(d)}
    if changed and db.execute(select(RelayNetwork.id).join(Domain, RelayNetwork.domain_id == Domain.id)
                              .where(func.lower(Domain.name).in_(changed)).limit(1)).first():
        ensure_imported(db)
        apply_changes(db)


@router.get("/domain-options", response_model=list[str])
def domain_options(_: AdminUser, db: DB) -> list[str]:
    return list(db.execute(select(Domain.name).order_by(Domain.name)).scalars())


@router.get("", response_model=list[UserOut])
def list_users(_: AdminUser, db: DB) -> list[UserOut]:
    return [out(u) for u in db.execute(select(User).order_by(User.email)).scalars()]


@router.post("", response_model=UserCreated, status_code=201)
def create_user(body: UserIn, request: Request, admin: AdminUser, db: DB) -> UserCreated:
    email = body.email.strip().lower()
    if db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "email_exists")
    password = body.password or generate_password()
    before = customer_sender_domains(db)
    u = User(
        email=email, name=body.name.strip(), role=body.role, language=body.language,
        is_active=body.is_active, password_hash=hash_password(password),
    )
    u.domains = _domains(db, body.domains)
    db.add(u)
    db.flush()
    _relock(db, before)
    audit(db, request, admin, "user_created", email, role=body.role, domains=body.domains)
    db.commit()
    return UserCreated(**out(u).model_dump(), password=None if body.password else password)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserIn, request: Request, admin: AdminUser, db: DB) -> UserOut:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if u.id == admin.id and (body.role != "admin" or not body.is_active):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_demote_self")
    before = customer_sender_domains(db)
    u.email = body.email.strip().lower()
    u.name = body.name.strip()
    u.role = body.role
    u.language = body.language
    u.is_active = body.is_active
    u.domains = _domains(db, body.domains)
    if body.password:
        u.password_hash = hash_password(body.password)
        db.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
    if not u.is_active:
        db.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
    db.flush()
    _relock(db, before)
    audit(db, request, admin, "user_updated", u.email, role=u.role, domains=body.domains, active=u.is_active)
    db.commit()
    return out(u)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_delete_self")
    before = customer_sender_domains(db)
    audit(db, request, admin, "user_deleted", u.email)
    db.delete(u)
    db.flush()
    _relock(db, before)
    db.commit()


@router.post("/{user_id}/2fa/reset", response_model=UserOut)
def reset_two_factor(user_id: int, request: Request, admin: AdminUser, db: DB) -> UserOut:
    """For a user who lost their phone and recovery codes: turns 2FA off and logs
    them out. If 2FA is required they set it up again at the next login."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    twofactor.disable(u)
    db.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
    audit(db, request, admin, "2fa_reset", u.email)
    db.commit()
    return out(u)
