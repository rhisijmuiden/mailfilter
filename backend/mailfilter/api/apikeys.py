"""Managing API keys in the web app (session login only: a key cannot make keys).

Everyone can make keys for themselves; a key acts with its owner's rights.
Admins see every key, can make keys for a customer account and revoke any key.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..apikeys import AREAS, RATE_LIMIT, clean_ips, clean_scopes, new_token
from ..audit import audit
from ..deps import DB, CurrentUser
from ..models import ApiKey, User

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

MAX_KEYS_PER_USER = 50


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: dict[str, str] = {}
    allowed_ips: list[str] = Field(default=[], max_length=50)
    expires_at: datetime | None = None
    owner: str | None = Field(default=None, max_length=320)  # admins: make the key for this account


class KeyOut(BaseModel):
    id: int
    name: str
    hint: str
    owner: str
    owner_role: str
    owner_domains: list[str]
    scopes: dict[str, str]
    allowed_ips: list[str]
    expires_at: datetime | None
    expired: bool
    created_at: datetime
    created_by: str
    last_used_at: datetime | None
    last_used_ip: str | None


class KeyCreated(KeyOut):
    key: str  # shown once


class AreaOut(BaseModel):
    key: str
    write: bool
    admin_only: bool


class AreasOut(BaseModel):
    areas: list[AreaOut]
    rate_limit: int


def _out(k: ApiKey) -> KeyOut:
    now = datetime.now(timezone.utc)
    return KeyOut(
        id=k.id, name=k.name, hint=k.hint, owner=k.user.email, owner_role=k.user.role, owner_domains=k.user.domain_names,
        scopes=k.scopes or {}, allowed_ips=list(k.allowed_ips or []), expires_at=k.expires_at,
        expired=k.expires_at is not None and k.expires_at <= now, created_at=k.created_at, created_by=k.created_by,
        last_used_at=k.last_used_at, last_used_ip=str(k.last_used_ip) if k.last_used_ip else None,
    )


def _visible(db, user: User, key_id: int) -> ApiKey:
    k = db.get(ApiKey, key_id)
    if k is None or (not user.is_admin and k.user_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return k


def _expiry(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expiry_in_past")
    return value


def _owner(db, user: User, email: str | None) -> User:
    if not email or email.strip().lower() == user.email.lower():
        return user
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_only")
    owner = db.execute(select(User).where(func.lower(User.email) == email.strip().lower())).scalar_one_or_none()
    if owner is None or not owner.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_user")
    return owner


@router.get("/areas", response_model=AreasOut)
def areas(_: CurrentUser) -> AreasOut:
    return AreasOut(areas=[AreaOut(key=a.key, write=a.write, admin_only=a.admin_only) for a in AREAS], rate_limit=RATE_LIMIT)


@router.get("", response_model=list[KeyOut])
def list_keys(user: CurrentUser, db: DB) -> list[KeyOut]:
    q = select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    if not user.is_admin:
        q = q.where(ApiKey.user_id == user.id)
    return [_out(k) for k in db.execute(q).scalars()]


@router.post("", response_model=KeyCreated, status_code=201)
def create_key(body: KeyIn, request: Request, user: CurrentUser, db: DB) -> KeyCreated:
    owner = _owner(db, user, body.owner)
    count = db.execute(select(func.count()).select_from(ApiKey).where(ApiKey.user_id == owner.id)).scalar_one()
    if count >= MAX_KEYS_PER_USER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "too_many_keys")
    token, digest, hint = new_token()
    k = ApiKey(user_id=owner.id, name=body.name.strip(), token_hash=digest, hint=hint,
               scopes=clean_scopes(body.scopes, owner), allowed_ips=clean_ips(body.allowed_ips),
               expires_at=_expiry(body.expires_at), created_by=user.email)
    db.add(k)
    db.flush()
    db.refresh(k)
    audit(db, request, user, "api_key_created", k.name, owner=owner.email, scopes=k.scopes, allowed_ips=k.allowed_ips,
          domain=owner.domain_names[0] if not owner.is_admin and owner.domain_names else None)
    db.commit()
    return KeyCreated(**_out(k).model_dump(), key=token)


@router.put("/{key_id}", response_model=KeyOut)
def update_key(key_id: int, body: KeyIn, request: Request, user: CurrentUser, db: DB) -> KeyOut:
    k = _visible(db, user, key_id)
    k.name = body.name.strip()
    k.scopes = clean_scopes(body.scopes, k.user)
    k.allowed_ips = clean_ips(body.allowed_ips)
    if body.expires_at != k.expires_at:
        k.expires_at = _expiry(body.expires_at)
    audit(db, request, user, "api_key_updated", k.name, owner=k.user.email, scopes=k.scopes, allowed_ips=k.allowed_ips)
    db.commit()
    return _out(k)


@router.delete("/{key_id}", status_code=204)
def delete_key(key_id: int, request: Request, user: CurrentUser, db: DB) -> None:
    k = _visible(db, user, key_id)
    audit(db, request, user, "api_key_revoked", k.name, owner=k.user.email, hint=k.hint)
    db.delete(k)
    db.commit()
