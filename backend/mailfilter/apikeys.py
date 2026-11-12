"""API keys for the public REST API under /api/v1.

A key is sent as `Authorization: Bearer mfk_...`. It acts as the user who owns
it (admins: every domain, customers: their own domains), and only for the
areas and levels in its scopes. Keys work on /api/v1 only, and /api/v1 takes
keys only (no session cookie), so cross-site requests are not a concern there.

What a key can never do: manage API keys, updates, backups, the cluster or
system settings. Paths outside AREAS answer 403 not_available_in_api, so a
leaked key cannot give itself more rights.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiKey, User
from .security import token_hash

PREFIX = "/api/v1"
TOKEN_PREFIX = "mfk_"
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
RATE_LIMIT = 600  # requests per key per minute
LAST_USED_EVERY = timedelta(minutes=1)


@dataclass(frozen=True)
class Area:
    key: str
    path: re.Pattern[str]
    write: bool = True  # False: there is nothing to change in this area
    admin_only: bool = False


def _a(key: str, path: str, **kw) -> Area:
    return Area(key, re.compile(rf"^{path}/?$"), **kw)


# Order is the order in the UI and the documentation.
AREAS: tuple[Area, ...] = (
    _a("domains", r"/gateway/domains(/\d+(/records)?)?"),
    _a("relay", r"/gateway/relay-networks(/\d+)?"),
    _a("smtp", r"/gateway/smtp-accounts(/\d+(/password)?)?"),
    _a("quarantine", r"/quarantine(/[^/]+/(preview|release|delete))?"),
    _a("trace", r"/trace(/stats|/[^/]+)?", write=False),
    _a("lists", r"/lists(/options|/\d+)?"),
    _a("users", r"/users(/domain-options|/\d+)?", admin_only=True),
    _a("audit", r"/audit(/filters|/export\.csv)?", write=False),
)
AREA_KEYS = tuple(a.key for a in AREAS)
# Always open to any valid key: information about the key itself.
OPEN_PATHS = ("/me",)


def area_for(path: str) -> Area | None:
    rel = path[len(PREFIX):] if path.startswith(PREFIX) else path
    return next((a for a in AREAS if a.path.match(rel)), None)


def clean_scopes(scopes: dict[str, str], user: User) -> dict[str, str]:
    """Only known areas and levels; write where an area has nothing to write is read."""
    out: dict[str, str] = {}
    for a in AREAS:
        level = scopes.get(a.key)
        if level not in ("read", "write"):
            continue
        if a.admin_only and not user.is_admin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scope_admin_only:{a.key}")
        out[a.key] = "write" if level == "write" and a.write else "read"
    if not out:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scopes_required")
    return out


def clean_ips(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        v = v.strip()
        if not v:
            continue
        try:
            net = ipaddress.ip_network(v, strict=False)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid_ip:{v}") from None
        s = str(net.network_address) if net.num_addresses == 1 else str(net)
        if s not in out:
            out.append(s)
    return out


def new_token() -> tuple[str, str, str]:
    """(key shown once, its hash, its hint)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, token_hash(token), token[:12]


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def hit(self, key_id: int) -> int | None:
        """None when allowed, else seconds to wait."""
        now = time.monotonic()
        q = self._hits[key_id]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(1, int(q[0] + self.window - now) + 1)
        q.append(now)
        return None


rate_limiter = RateLimiter()


def _deny(code: int, detail: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(code, detail, headers=headers)


def bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.headers.get("x-api-key") or None


def authenticate(request: Request, db: Session, ip: str | None) -> User:
    """The owner of the key on this request, after every check. Raises HTTPException."""
    token = bearer(request)
    challenge = {"WWW-Authenticate": "Bearer"}
    if not token:
        raise _deny(status.HTTP_401_UNAUTHORIZED, "api_key_required", challenge)
    key = db.execute(select(ApiKey).where(ApiKey.token_hash == token_hash(token))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if key is None or not key.user.is_active:
        raise _deny(status.HTTP_401_UNAUTHORIZED, "invalid_api_key", challenge)
    if key.expires_at is not None and key.expires_at <= now:
        raise _deny(status.HTTP_401_UNAUTHORIZED, "api_key_expired", challenge)
    if key.allowed_ips:
        addr = ipaddress.ip_address(ip) if ip else None
        if addr is None or not any(addr in ipaddress.ip_network(n, strict=False) for n in key.allowed_ips):
            raise _deny(status.HTTP_403_FORBIDDEN, "ip_not_allowed")
    wait = rate_limiter.hit(key.id)
    if wait is not None:
        raise _deny(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", {"Retry-After": str(wait)})

    path = request.url.path.rstrip("/")
    if path[len(PREFIX):] not in OPEN_PATHS:
        area = area_for(path)
        if area is None:
            raise _deny(status.HTTP_403_FORBIDDEN, "not_available_in_api")
        need = "read" if request.method in READ_METHODS else "write"
        have = (key.scopes or {}).get(area.key)
        if have is None or (need == "write" and have != "write"):
            raise _deny(status.HTTP_403_FORBIDDEN, f"scope_missing:{area.key}:{need}")
        if area.admin_only and not key.user.is_admin:
            raise _deny(status.HTTP_403_FORBIDDEN, "admin_only")

    request.state.api_key = key
    if key.last_used_at is None or now - key.last_used_at >= LAST_USED_EVERY or str(key.last_used_ip or "") != (ip or ""):
        key.last_used_at, key.last_used_ip = now, ip
        db.commit()
    return key.user


def api_key_of(request: Request | None) -> ApiKey | None:
    return getattr(request.state, "api_key", None) if request is not None else None
