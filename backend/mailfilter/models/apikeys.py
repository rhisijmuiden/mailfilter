"""API keys for the public REST API (/api/v1). See mailfilter.apikeys."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .core import User


class ApiKey(Base):
    """A key acts as its owner (admin: everything, customer: own domains),
    limited further by its scopes, allowed IP addresses and expiry date.

    Only the SHA-256 of the key is stored; `hint` is its first characters so
    people can tell keys apart."""

    __tablename__ = "api_key"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(20))
    # {"domains": "read", "relay": "write", ...}; see mailfilter.apikeys.AREAS
    scopes: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Empty: any address. Otherwise IP addresses / networks the key may be used from.
    allowed_ips: Mapped[list] = mapped_column(JSONB, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(320), default="")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user: Mapped[User] = relationship(lazy="joined")
