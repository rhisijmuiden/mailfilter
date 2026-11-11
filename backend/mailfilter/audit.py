from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from .deps import client_ip
from .models import AuditLog, User


def audit(db: Session, request: Request | None, user: User | None, action: str, target: str = "", **detail) -> None:
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            target=target[:300],
            detail=detail,
            ip=client_ip(request) if request else None,
        )
    )
