"""Settings > Notifications: alert channels (e-mail, Telegram, webhook), which
alert goes where, a test button per channel and the recent history."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import notify
from ..audit import audit
from ..deps import DB, AdminUser
from ..settings_store import get_secret

router = APIRouter(prefix="/settings/notifications", tags=["settings"])

EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
ChannelName = Literal["email", "telegram", "webhook"]


class TypeIn(BaseModel):
    channels: list[ChannelName] = []
    threshold: int = Field(default=0, ge=0, le=100000)


class NotifyIn(BaseModel):
    language: Literal["en", "nl"] = "en"
    repeat_hours: int = Field(default=12, ge=0, le=168)
    send_resolved: bool = True
    email_enabled: bool = True
    email_to: list[str] = Field(default=[], max_length=20)
    telegram_enabled: bool = False
    telegram_chat_id: str = Field(default="", max_length=64, pattern=r"^(-?[0-9]+|@[A-Za-z0-9_]{4,})?$")
    telegram_token: str | None = Field(default=None, max_length=100, pattern=r"^([0-9]+:[A-Za-z0-9_-]+)?$")
    webhook_enabled: bool = False
    webhook_url: str = Field(default="", max_length=2000, pattern=r"^(https?://\S+)?$")
    webhook_format: Literal["json", "slack", "discord"] = "json"
    webhook_secret: str | None = Field(default=None, max_length=200)
    types: dict[str, TypeIn] = {}


class NotifyOut(BaseModel):
    language: str
    repeat_hours: int
    send_resolved: bool
    email_enabled: bool
    email_to: list[str]
    email_default: list[str]
    telegram_enabled: bool
    telegram_chat_id: str
    has_telegram_token: bool
    webhook_enabled: bool
    webhook_url: str
    webhook_format: str
    has_webhook_secret: bool
    types: dict[str, dict]
    type_order: list[str]
    thresholds: list[str]
    active: list[dict]


def _settings(body: NotifyIn) -> notify.NotifySettings:
    to = [a.strip().lower() for a in body.email_to if a.strip()]
    for a in to:
        if not re.match(EMAIL, a):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"notify_bad_email:{a}")
    types = {t: notify.TypeSettings(channels=list(v.channels), threshold=v.threshold)
             for t, v in body.types.items() if t in notify.TYPES}
    return notify.NotifySettings(
        language=body.language, repeat_hours=body.repeat_hours, send_resolved=body.send_resolved,
        email_enabled=body.email_enabled, email_to=to,
        telegram_enabled=body.telegram_enabled, telegram_chat_id=body.telegram_chat_id.strip(),
        webhook_enabled=body.webhook_enabled, webhook_url=body.webhook_url.strip(), webhook_format=body.webhook_format,
        types=types,
    )


def _out(db) -> NotifyOut:
    s = notify.load_settings(db)
    default = notify.email_recipients(db, notify.NotifySettings())
    return NotifyOut(
        language=s.language, repeat_hours=s.repeat_hours, send_resolved=s.send_resolved,
        email_enabled=s.email_enabled, email_to=s.email_to, email_default=default,
        telegram_enabled=s.telegram_enabled, telegram_chat_id=s.telegram_chat_id,
        has_telegram_token=bool(get_secret(db, notify.TELEGRAM_TOKEN)),
        webhook_enabled=s.webhook_enabled, webhook_url=s.webhook_url, webhook_format=s.webhook_format,
        has_webhook_secret=bool(get_secret(db, notify.WEBHOOK_SECRET)),
        types={t: {"channels": v.channels, "threshold": v.threshold} for t, v in s.types.items()},
        type_order=list(notify.TYPES), thresholds=list(notify.DEFAULT_THRESHOLDS),
        active=notify.active(db),
    )


@router.get("", response_model=NotifyOut)
def get_notifications(db: DB, _: AdminUser) -> NotifyOut:
    return _out(db)


@router.put("", response_model=NotifyOut)
def put_notifications(body: NotifyIn, request: Request, db: DB, user: AdminUser) -> NotifyOut:
    s = _settings(body)
    notify.save_settings(db, s, telegram_token=body.telegram_token, webhook_secret=body.webhook_secret)
    audit(db, request, user, "settings.notifications", "",
          email=s.email_enabled, telegram=s.telegram_enabled, webhook=s.webhook_enabled)
    db.commit()
    return _out(db)


class TestIn(NotifyIn):
    channel: ChannelName


@router.post("/test")
def test_channel(body: TestIn, db: DB, _: AdminUser) -> dict:
    """Send a test alert to one channel with the settings in the form (saved or not)."""
    s = _settings(body)
    a = notify.Alert("test", "test", "test", "info", event="test")
    try:
        if body.channel == "email":
            notify.send_email(db, s, a)
        elif body.channel == "telegram":
            notify.send_telegram(db, s, a, token=body.telegram_token or None)
        else:
            notify.send_webhook(db, s, a, secret=body.webhook_secret)
    except notify.ChannelError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{e.code}:{e.detail}" if e.detail else e.code) from e
    return {"ok": True}


class ChatsIn(BaseModel):
    telegram_token: str | None = Field(default=None, max_length=100, pattern=r"^([0-9]+:[A-Za-z0-9_-]+)?$")


@router.post("/telegram/chats")
def telegram_chats(body: ChatsIn, db: DB, _: AdminUser) -> dict:
    """Chats that recently sent the bot a message, so the admin does not need to look up a chat id."""
    token = body.telegram_token or get_secret(db, notify.TELEGRAM_TOKEN) or ""
    try:
        return {"chats": notify.telegram_chats(token)}
    except notify.ChannelError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{e.code}:{e.detail}" if e.detail else e.code) from e


@router.get("/history")
def get_history(db: DB, _: AdminUser) -> dict:
    return {"items": notify.history(db)}
