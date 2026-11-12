"""Login, logout and the logged-in user's own profile."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import twofactor
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
    two_factor_enabled: bool = False
    reseller: str | None = None  # role "reseller": the reseller's name
    # Policy requires 2FA but the user has not set it up: only the setup works.
    two_factor_setup_required: bool = False


class ChallengeOut(BaseModel):
    two_factor_required: Literal[True] = True
    challenge: str


class TwoFactorLoginIn(BaseModel):
    challenge: str = Field(max_length=1000)
    code: str = Field(max_length=40)


class CodeIn(BaseModel):
    code: str = Field(max_length=40)


class DisableIn(BaseModel):
    password: str = Field(max_length=500)
    code: str = Field(max_length=40)


class SetupOut(BaseModel):
    secret: str
    uri: str
    qr_svg: str


class RecoveryCodesOut(BaseModel):
    codes: list[str]


class TwoFactorStatus(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    recovery_codes_left: int
    required: bool


class MeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    language: Literal["en", "nl"] | None = None


class PasswordIn(BaseModel):
    current: str = Field(max_length=500)
    new: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=500)


def me_out(user: User, db=None) -> MeOut:
    setup = bool(db is not None and not user.two_factor_enabled and twofactor.required_for(db, user))
    return MeOut(
        id=user.id, email=user.email, name=user.name, role=user.role, language=user.language, domains=user.domain_names,
        two_factor_enabled=user.two_factor_enabled, two_factor_setup_required=setup,
        reseller=_reseller_name(db, user),
    )


def _reseller_name(db, user: User) -> str | None:
    if user.role != "reseller" or user.reseller_id is None or db is None:
        return None
    from ..models import Reseller

    r = db.get(Reseller, user.reseller_id)
    return r.name if r else None


def _start_session(user: User, request: Request, response: Response, db, method: str) -> MeOut:
    login_throttle.reset(f"user:{user.email.lower()}")
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
    audit(db, request, user, "login", user.email, method=method)
    db.commit()
    response.set_cookie(
        COOKIE, token, max_age=s.session_hours * 3600, httponly=True, secure=s.cookie_secure, samesite="strict", path="/"
    )
    return me_out(user, db)


@router.post("/login", response_model=MeOut | ChallengeOut)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> MeOut | ChallengeOut:
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
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        db.commit()
    if user.two_factor_enabled:
        # Password is right; the session only starts after the code step.
        return ChallengeOut(challenge=twofactor.make_challenge(user))
    return _start_session(user, request, response, db, "password")


@router.post("/login/2fa", response_model=MeOut)
def login_two_factor(body: TwoFactorLoginIn, request: Request, response: Response, db: DB) -> MeOut:
    ip = client_ip(request) or "?"
    found = twofactor.challenge_user_id(body.challenge)
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "challenge_expired")
    user = db.get(User, found[0])
    if user is None or not user.is_active or not user.two_factor_enabled or not twofactor.challenge_matches(user, found[1]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "challenge_expired")
    key = f"user:{user.email.lower()}"
    if login_throttle.blocked(f"ip:{ip}", key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts")
    left = len(user.recovery_codes or [])
    if not twofactor.verify_user_code(user, body.code):
        login_throttle.fail(f"ip:{ip}", key)
        audit(db, request, user, "login_2fa_failed", user.email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_code")
    method = "recovery_code" if len(user.recovery_codes or []) < left else "totp"
    return _start_session(user, request, response, db, method)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DB) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, db: DB) -> MeOut:
    return me_out(user, db)


@router.patch("/me", response_model=MeOut)
def update_me(body: MeUpdate, user: CurrentUser, db: DB) -> MeOut:
    if body.name is not None:
        user.name = body.name.strip()
    if body.language is not None:
        user.language = body.language
    db.add(user)
    db.commit()
    return me_out(user, db)


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


# ---- two-factor setup for the logged-in user ----------------------------------------
@router.get("/2fa", response_model=TwoFactorStatus)
def two_factor_status(user: CurrentUser, db: DB) -> TwoFactorStatus:
    return TwoFactorStatus(
        enabled=user.two_factor_enabled, enabled_at=user.totp_enabled_at,
        recovery_codes_left=len(user.recovery_codes or []) if user.two_factor_enabled else 0,
        required=twofactor.required_for(db, user),
    )


@router.post("/2fa/setup", response_model=SetupOut)
def two_factor_setup(user: CurrentUser, db: DB) -> SetupOut:
    """Starts (or restarts) setting up an authenticator app. Nothing changes for
    logging in until the first code is confirmed."""
    if user.two_factor_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two_factor_already_enabled")
    secret = twofactor.new_secret()
    user.totp_secret = twofactor.encrypt_secret(secret)
    user.totp_last_step = None
    db.commit()
    uri = twofactor.provisioning_uri(secret, user.email)
    return SetupOut(secret=secret, uri=uri, qr_svg=twofactor.qr_svg(uri))


@router.post("/2fa/enable", response_model=RecoveryCodesOut)
def two_factor_enable(body: CodeIn, request: Request, user: CurrentUser, db: DB) -> RecoveryCodesOut:
    if user.two_factor_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two_factor_already_enabled")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "two_factor_not_started")
    step = twofactor.match_step(twofactor.decrypt_secret(user.totp_secret), body.code)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_code")
    user.totp_last_step = step
    user.totp_enabled_at = datetime.now(timezone.utc)
    codes = twofactor.new_recovery_codes(user)
    # Other sessions were logged in with the password only: log them out.
    keep = token_hash(request.cookies.get(COOKIE, ""))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != keep))
    audit(db, request, user, "2fa_enabled", user.email)
    db.commit()
    return RecoveryCodesOut(codes=codes)


@router.post("/2fa/recovery-codes", response_model=RecoveryCodesOut)
def two_factor_new_codes(body: CodeIn, request: Request, user: CurrentUser, db: DB) -> RecoveryCodesOut:
    if not user.two_factor_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "two_factor_not_enabled")
    if not twofactor.verify_user_code(user, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_code")
    codes = twofactor.new_recovery_codes(user)
    audit(db, request, user, "2fa_recovery_codes_renewed", user.email)
    db.commit()
    return RecoveryCodesOut(codes=codes)


@router.post("/2fa/disable", status_code=204)
def two_factor_disable(body: DisableIn, request: Request, user: CurrentUser, db: DB) -> None:
    if not user.two_factor_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "two_factor_not_enabled")
    if twofactor.required_for(db, user):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "two_factor_required_by_policy")
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong_current_password")
    if not twofactor.verify_user_code(user, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_code")
    twofactor.disable(user)
    audit(db, request, user, "2fa_disabled", user.email)
    db.commit()
