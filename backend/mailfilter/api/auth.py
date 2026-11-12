"""Login, logout and the logged-in user's own profile."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from ..audit import audit
from ..deps import COOKIE, DB, CurrentUser, client_ip
from ..models import AuthSession, User
from ..security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    login_throttle,
    needs_rehash,
    new_token,
    token_hash,
    verify_password,
)
from ..settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=500)


class MeOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    language: str
    domains: list[str]


class MeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    language: Literal["en", "nl"] | None = None


class PasswordIn(BaseModel):
    current: str = Field(max_length=500)
    new: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=500)


def me_out(user: User) -> MeOut:
    return MeOut(id=user.id, email=user.email, name=user.name, role=user.role, language=user.language, domains=user.domain_names)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> MeOut:
    email = body.email.strip().lower()
    ip = client_ip(request) or "?"
    if login_throttle.blocked(f"ip:{ip}", f"user:{email}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts")
    user = db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
    if not verify_password(user.password_hash if user else None, body.password) or not user or not user.is_active:
        login_throttle.fail(f"ip:{ip}", f"user:{email}")
        audit(db, request, user, "login_failed", email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_credentials")
    login_throttle.reset(f"user:{email}")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    s = get_settings()
    token, digest = new_token()
    db.add(
        AuthSession(
            token_hash=digest,
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_hours),
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now(timezone.utc)))
    user.last_login_at = datetime.now(timezone.utc)
    audit(db, request, user, "login", email)
    db.commit()
    response.set_cookie(
        COOKIE, token, max_age=s.session_hours * 3600, httponly=True, secure=s.cookie_secure, samesite="strict", path="/"
    )
    return me_out(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DB) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return me_out(user)


@router.patch("/me", response_model=MeOut)
def update_me(body: MeUpdate, user: CurrentUser, db: DB) -> MeOut:
    if body.name is not None:
        user.name = body.name.strip()
    if body.language is not None:
        user.language = body.language
    db.add(user)
    db.commit()
    return me_out(user)


@router.post("/me/password", status_code=204)
def change_password(body: PasswordIn, request: Request, user: CurrentUser, db: DB) -> None:
    if not verify_password(user.password_hash, body.current):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong_current_password")
    user.password_hash = hash_password(body.new)
    # Log out every other session of this user.
    keep = token_hash(request.cookies.get(COOKIE, ""))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != keep))
    audit(db, request, user, "password_changed", user.email)
    db.add(user)
    db.commit()
