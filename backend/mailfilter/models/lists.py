"""Allow and block lists people manage themselves, and the daily quarantine
report settings. The lists are written to Rspamd maps by mailfilter.lists."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .core import Domain


class SenderListEntry(Base):
    """One sender on an allow or block list.

    ``recipient`` says whose list it is: a mailbox ("jan@klant.nl"), a whole
    domain ("klant.nl") or "*" for every domain (admins only). ``sender`` is an
    address ("info@shop.example") or a domain ("shop.example")."""

    __tablename__ = "sender_list_entry"
    __table_args__ = (UniqueConstraint("recipient", "sender", name="uq_sender_list_pair"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list: Mapped[str] = mapped_column(String(10))  # allow | block
    recipient: Mapped[str] = mapped_column(String(320))
    sender: Mapped[str] = mapped_column(String(320))
    # The customer domain this entry belongs to (None for "*"), for scoping and cleanup.
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("domain.id", ondelete="CASCADE"), nullable=True, index=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    domain: Mapped[Domain | None] = relationship(lazy="joined")


class DigestDomain(Base):
    """Per-domain switch for the daily quarantine report (no row = on)."""

    __tablename__ = "digest_domain"
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DigestSent(Base):
    """Which recipient got a report when, so a message is reported once as new."""

    __tablename__ = "digest_sent"
    recipient: Mapped[str] = mapped_column(String(320), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
