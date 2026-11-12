"""Settings > E-mail sending: how the app sends its own mail, plus a test message."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import mailer
from ..audit import audit
from ..deps import DB, AdminUser

router = APIRouter(prefix="/settings/smtp", tags=["settings"])

EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class SmtpOut(BaseModel):
    mode: Literal["local", "smtp"]
    host: str
    port: int
    security: Literal["starttls", "tls", "none"]
    username: str
    has_password: bool
    verify_tls: bool
    from_address: str
    from_name: str
    reply_to: str
    default_from: str


class SmtpIn(BaseModel):
    mode: Literal["local", "smtp"] = "local"
    host: str = Field(default="", max_length=253, pattern=r"^[A-Za-z0-9.:\[\]-]*$")
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls", "none"] = "starttls"
    username: str = Field(default="", max_length=320)
    password: str | None = Field(default=None, max_length=500)
    clear_password: bool = False
    verify_tls: bool = True
    from_address: str = Field(default="", max_length=320, pattern=rf"^$|{EMAIL}")
    from_name: str = Field(default="Mailfilter", max_length=100)
    reply_to: str = Field(default="", max_length=320, pattern=rf"^$|{EMAIL}")


def _out(db) -> SmtpOut:
    cfg = mailer.load_config(db)
    return SmtpOut(**{k: getattr(cfg, k) for k in SmtpOut.model_fields if hasattr(cfg, k)},
                   has_password=mailer.has_password(db), default_from=cfg.sender)


def _cfg(body: SmtpIn) -> mailer.SmtpConfig:
    if body.mode == "smtp" and not body.host:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "smtp_no_host")
    return mailer.SmtpConfig(
        mode=body.mode, host=body.host.strip(), port=body.port, security=body.security,
        username=body.username.strip(), verify_tls=body.verify_tls, from_address=body.from_address.strip(),
        from_name=body.from_name.strip(), reply_to=body.reply_to.strip(),
    )


@router.get("", response_model=SmtpOut)
def get_smtp(db: DB, _: AdminUser) -> SmtpOut:
    return _out(db)


@router.put("", response_model=SmtpOut)
def put_smtp(body: SmtpIn, request: Request, db: DB, user: AdminUser) -> SmtpOut:
    mailer.save_config(db, _cfg(body), password=body.password, clear_password=body.clear_password)
    audit(db, request, user, "settings.smtp", body.mode, host=body.host, port=body.port, security=body.security)
    db.commit()
    return _out(db)


class TestIn(SmtpIn):
    to: str = Field(max_length=320, pattern=EMAIL)


@router.post("/test")
def test_smtp(body: TestIn, db: DB, user: AdminUser) -> dict:
    """Send a test message with the settings in the form (saved or not)."""
    cfg = _cfg(body)
    password = body.password if body.password else (None if not body.clear_password else "")
    try:
        msgid = mailer.send(
            db, body.to,
            "Mailfilter test message / testbericht",
            "This is a test message from Mailfilter. Sending works.\n\n"
            "Dit is een testbericht van Mailfilter. Verzenden werkt.\n",
            cfg=cfg, password=password,
        )
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{e.code}:{e.detail}" if e.detail else e.code) from e
    return {"ok": True, "message_id": msgid}
