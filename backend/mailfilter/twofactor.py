"""Two-factor login with TOTP (RFC 6238, the codes from an authenticator app)
and one-time recovery codes.

The TOTP secret is stored encrypted with APP_SECRET. Between the password step
and the code step the browser holds a short-lived signed challenge, so no
session exists until both steps succeeded.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import struct
import time
from typing import Literal
from urllib.parse import quote

from sqlalchemy.orm import Session

from .models import User
from .settings import get_settings
from .settings_store import _fernet, get_setting, set_setting

ISSUER = "Mailfilter"
STEP = 30
DIGITS = 6
WINDOW = 1  # accept one step before and after, for clock drift
RECOVERY_COUNT = 10
CHALLENGE_SECONDS = 300

Policy = Literal["off", "admins", "all"]
POLICY_KEY = "security"


# ---- TOTP -----------------------------------------------------------------------
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def totp(secret: str, at: float | None = None) -> str:
    return _code_at(secret, int((time.time() if at is None else at) // STEP))


def match_step(secret: str, code: str, at: float | None = None) -> int | None:
    """The time step the code belongs to, or None when it is wrong."""
    code = "".join(c for c in code if c.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((time.time() if at is None else at) // STEP)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(ISSUER) + ":" + quote(account, safe="@")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg(data: str) -> str:
    import segno

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True, dark="#0f172a", light="#ffffff")
    return buf.getvalue().decode()


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def verify_user_code(user: User, code: str) -> bool:
    """Checks a TOTP code (no replays) or uses up a recovery code."""
    if not user.totp_secret:
        return False
    step = match_step(decrypt_secret(user.totp_secret), code)
    if step is not None:
        if user.totp_last_step is not None and step <= user.totp_last_step:
            return False
        user.totp_last_step = step
        return True
    return use_recovery_code(user, code)


# ---- recovery codes -------------------------------------------------------------
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _normalize(code: str) -> str:
    return "".join(c for c in code.lower() if c.isalnum())


def _hash(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    codes = ["-".join("".join(secrets.choice(_ALPHABET) for _ in range(5)) for _ in range(2)) for _ in range(RECOVERY_COUNT)]
    user.recovery_codes = [_hash(c) for c in codes]
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    if len(_normalize(code)) != 10:
        return False
    h = _hash(code)
    left = list(user.recovery_codes or [])
    if not any(hmac.compare_digest(h, x) for x in left):
        return False
    user.recovery_codes = [x for x in left if x != h]
    return True


def disable(user: User) -> None:
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.recovery_codes = []


# ---- login challenge ------------------------------------------------------------
def _sign(payload: bytes) -> str:
    key = hashlib.sha256(b"mailfilter-2fa:" + get_settings().app_secret.encode()).digest()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def _fingerprint(user: User) -> str:
    # Changes when the password or the 2FA setup changes, which voids old challenges.
    return hashlib.sha256(f"{user.password_hash}|{user.totp_secret}".encode()).hexdigest()[:16]


def make_challenge(user: User) -> str:
    payload = json.dumps({"u": user.id, "e": int(time.time()) + CHALLENGE_SECONDS, "f": _fingerprint(user)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(payload)


def challenge_user_id(token: str) -> tuple[int, str] | None:
    """(user id, fingerprint) of a valid, unexpired challenge."""
    try:
        body, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        data = json.loads(payload)
        if data["e"] < time.time():
            return None
        return int(data["u"]), str(data["f"])
    except (ValueError, KeyError, TypeError):
        return None


def challenge_matches(user: User, fingerprint: str) -> bool:
    return hmac.compare_digest(_fingerprint(user), fingerprint)


# ---- policy ---------------------------------------------------------------------
def get_policy(db: Session) -> Policy:
    value = (get_setting(db, POLICY_KEY) or {}).get("require_2fa", "off")
    return value if value in ("off", "admins", "all") else "off"


def set_policy(db: Session, policy: Policy) -> None:
    data = dict(get_setting(db, POLICY_KEY) or {})
    data["require_2fa"] = policy
    set_setting(db, POLICY_KEY, data)


def required_for(db: Session, user: User) -> bool:
    policy = get_policy(db)
    return policy == "all" or (policy == "admins" and user.is_admin)
