"""Key/value settings in the database (table app_setting).

Secrets (API tokens, SMTP passwords) are encrypted with Fernet using APP_SECRET
from /etc/mailfilter/secrets.env, so a database dump alone does not leak them.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from .models import AppSetting
from .settings import get_settings


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(AppSetting, key)
    return default if row is None else row.value


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.flush()


def _fernet() -> Fernet:
    secret = get_settings().app_secret
    if not secret:
        raise RuntimeError("APP_SECRET is not set in /etc/mailfilter/secrets.env")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def set_secret(db: Session, key: str, value: str | None) -> None:
    set_setting(db, key, None if value is None else {"enc": _fernet().encrypt(value.encode()).decode()})


def get_secret(db: Session, key: str) -> str | None:
    value = get_setting(db, key)
    if not value:
        return None
    return _fernet().decrypt(value["enc"].encode()).decode()
