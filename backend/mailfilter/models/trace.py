"""Read-only mapping of the mail trace tables (written by mailfilter_tracer),
plus the decisions people take in quarantine."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class TraceMessage(Base):
    __tablename__ = "trace_message"
    # Created by migration 0002 (raw SQL), so Alembic autogenerate must not touch it.
    __table_args__ = {"info": {"skip_autogenerate": True}}
    queue_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    listener: Mapped[str | None] = mapped_column(Text)
    client_host: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(INET)
    sasl_user: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(BigInteger)
    spam_score: Mapped[float | None] = mapped_column(Float)
    spam_action: Mapped[str | None] = mapped_column(Text)
    symbols: Mapped[dict] = mapped_column(JSONB, default=dict)
    parent: Mapped[str | None] = mapped_column(Text)
    is_bounce: Mapped[bool] = mapped_column(Boolean, default=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    recipients: Mapped[list[TraceRecipient]] = relationship(lazy="selectin", order_by="TraceRecipient.address")


class TraceRecipient(Base):
    __tablename__ = "trace_recipient"
    __table_args__ = {"info": {"skip_autogenerate": True}}
    queue_id: Mapped[str] = mapped_column(ForeignKey("trace_message.queue_id", ondelete="CASCADE"), primary_key=True)
    address: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    dsn: Mapped[str | None] = mapped_column(Text)
    reply: Mapped[str | None] = mapped_column(Text)
    relay_host: Mapped[str | None] = mapped_column(Text)
    relay_ip: Mapped[str | None] = mapped_column(INET)
    transport: Mapped[str | None] = mapped_column(Text)
    delay: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    explain: Mapped[str | None] = mapped_column(Text)
    updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuarantineAction(Base):
    """Release or block decision on a quarantined message."""

    __tablename__ = "quarantine_action"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queue_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(20))  # released | deleted
    learned: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ham | spam | None
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    user_email: Mapped[str] = mapped_column(String(320), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
