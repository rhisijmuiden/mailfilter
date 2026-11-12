"""Audit log: who changed what, when and from where.

Every entry may carry the domain it is about, so customers can see the entries
for their own domains next to their own actions (see api/audit.py).
"""

from __future__ import annotations

import re

from fastapi import Request
from sqlalchemy.orm import Session

from .deps import client_ip
from .models import AuditLog, User

_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def domain_of(value: str | None) -> str | None:
    """The domain in a target such as "klant.nl" or "info@klant.nl"."""
    if not value:
        return None
    v = value.strip().lower().rsplit("@", 1)[-1].rstrip(".")
    return v if _DOMAIN.match(v) else None


def audit(
    db: Session, request: Request | None, user: User | None, action: str, target: str = "",
    *, domain: str | None = None, **detail,
) -> None:
    key = getattr(request.state, "api_key", None) if request is not None else None
    if key is not None:
        detail = {**detail, "api_key": key.name}
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            domain=(domain_of(domain) if domain else None) or domain_of(target) or domain_of(detail.get("domain")),
            action=action,
            target=target[:300],
            detail=detail,
            ip=client_ip(request) if request else None,
        )
    )
