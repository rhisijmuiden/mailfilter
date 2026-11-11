"""Password hashing, session tokens and simple login throttling."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Used to spend the same time on unknown e-mail addresses as on real ones.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> tuple[str, str]:
    """Returns (token for the cookie, hash for the database)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_password(length: int = 16) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class LoginThrottle:
    """At most `limit` failed logins per key (IP or e-mail) per `window` seconds."""

    def __init__(self, limit: int = 8, window: int = 900) -> None:
        self.limit, self.window = limit, window
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q = self._fails[key]
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, *keys: str) -> bool:
        return any(len(self._prune(k)) >= self.limit for k in keys)

    def fail(self, *keys: str) -> None:
        now = time.monotonic()
        for k in keys:
            self._prune(k).append(now)

    def reset(self, *keys: str) -> None:
        for k in keys:
            self._fails.pop(k, None)


login_throttle = LoginThrottle()
