"""Mailtrap mailboxes: mail from test environments is caught here instead of sent.

The caught mail itself lives on disk (Maildir, see mailfilter.mailtrap.store);
these tables hold the mailboxes and who may see them. Turned into server
configuration by mailfilter.gateway (mailfilter_config.MailTrap)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .core import Domain, User

# Users (often customers' developers) who may open a mailbox besides the admins
# and the users of the mailbox's customer domain.
mailtrap_user = Table(
    "mailtrap_user",
    Base.metadata,
    Column("mailtrap_id", ForeignKey("mailtrap.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
)


class MailTrap(Base):
    __tablename__ = "mailtrap"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("domain.id", ondelete="SET NULL"), nullable=True, index=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    smtp_login: Mapped[bool] = mapped_column(Boolean, default=False)
    imap: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(10), default="all")
    allow: Mapped[list] = mapped_column(JSONB, default=list)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    max_messages: Mapped[int] = mapped_column(Integer, default=1000)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    domain: Mapped[Domain | None] = relationship(lazy="joined")
    networks: Mapped[list["MailTrapNetwork"]] = relationship(lazy="selectin", order_by="MailTrapNetwork.id",
                                                              cascade="all, delete-orphan")
    users: Mapped[list[User]] = relationship(secondary=mailtrap_user, lazy="selectin", order_by=User.email)


class MailTrapNetwork(Base):
    """A test environment's IP address or network; its mail goes to the mailbox."""

    __tablename__ = "mailtrap_network"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailtrap_id: Mapped[int] = mapped_column(ForeignKey("mailtrap.id", ondelete="CASCADE"), index=True)
    cidr: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")
