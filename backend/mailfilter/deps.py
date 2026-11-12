"""FastAPI dependencies: database session, the logged-in user, role checks."""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import twofactor
from .db import get_db
from .models import AuthSession, User
from .security import token_hash

COOKIE = "mf_session"

DB = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    """The peer address. Caddy runs on the same host and sets X-Forwarded-For;
    uvicorn is started with --proxy-headers --forwarded-allow-ips 127.0.0.1."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def current_user(request: Request, db: DB) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_logged_in")
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash(token))).scalar_one_or_none()
    if sess is None or sess.expires_at < datetime.now(timezone.utc) or not sess.user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_logged_in")
    user = sess.user
    # When 2FA is required but not set up yet, only the setup itself works.
    if not user.two_factor_enabled and not _setup_allowed(request) and twofactor.required_for(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "two_factor_setup_required")
    return user


_SETUP_PATHS = ("/api/auth/me", "/api/auth/2fa", "/api/system/info")


def _setup_allowed(request: Request) -> bool:
    path = request.url.path.rstrip("/")
    return any(path == p or path.startswith(p + "/") for p in _SETUP_PATHS)


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def visible_domains(user: User) -> list[str] | None:
    """None means "all domains" (admin); otherwise the customer's own domains."""
    return None if user.is_admin else user.domain_names


def address_in_domains(address: str | None, domains: list[str] | None) -> bool:
    if domains is None:
        return True
    if not address or "@" not in address:
        return False
    return address.rsplit("@", 1)[1].lower() in {d.lower() for d in domains}
