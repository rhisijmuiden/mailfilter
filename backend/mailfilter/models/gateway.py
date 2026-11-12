"""Domains (routing, queues, limits), relay access, extra relay ports and
outbound throttling. Turned into server configuration by mailfilter.gateway
through the mailfilter_config package."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .core import Domain


class DomainRoute(Base):
    """Delivery settings for one domain (one row per Domain)."""

    __tablename__ = "domain_route"
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id", ondelete="CASCADE"), primary_key=True)
    # transport: receives mail and delivers it to `destinations`; relay: outbound only (no destinations).
    kind: Mapped[str] = mapped_column(String(10), default="transport")
    # Relay IPs and SMTP accounts linked to the domain may only send as @domain.
    sender_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tls: Mapped[str] = mapped_column(String(10), default="may")
    verify_recipients: Mapped[bool] = mapped_column(Boolean, default=True)
    concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_delay: Mapped[int | None] = mapped_column(Integer, nullable=True)
    out_per_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    out_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[Domain] = relationship(lazy="joined")
    destinations: Mapped[list["DomainDestination"]] = relationship(
        lazy="selectin", order_by="DomainDestination.position", cascade="all, delete-orphan",
        primaryjoin="DomainRoute.domain_id == foreign(DomainDestination.domain_id)",
    )


class DomainDestination(Base):
    """A mail server that receives clean mail for a domain; the first is tried first."""

    __tablename__ = "domain_destination"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=25)
    mx: Mapped[bool] = mapped_column(Boolean, default=False)
    protocol: Mapped[str] = mapped_column(String(10), default="smtp")


class RelayNetwork(Base):
    """An IP address or network that may send mail out through the gateway.

    domain_id links it to a customer's domain, so that customer can see and
    manage it; admin-only networks have no domain."""

    __tablename__ = "relay_network"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidr: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("domain.id", ondelete="SET NULL"), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_broad: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    domain: Mapped[Domain | None] = relationship(lazy="joined")


class SmtpAccount(Base):
    """A login for sending mail through the gateway (ports 587/465 and extra relay ports).

    Belongs to one domain, so customers can manage the accounts of their own
    domains. Dovecot checks the password against password_hash."""

    __tablename__ = "smtp_account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id", ondelete="CASCADE"), index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    domain: Mapped[Domain] = relationship(lazy="joined")


class RelayPort(Base):
    __tablename__ = "relay_port"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    port: Mapped[int] = mapped_column(Integer, unique=True)
    tls: Mapped[str] = mapped_column(String(10), default="may")
    description: Mapped[str] = mapped_column(String(200), default="")


class DestinationPolicy(Base):
    """Throttled outbound queue for a remote domain (e.g. gmail.com)."""

    __tablename__ = "destination_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_delay: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class AddressRoute(Base):
    """Deliver one address (or a domain that is not on the gateway) elsewhere."""

    __tablename__ = "address_route"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(320), unique=True)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=25)
    mx: Mapped[bool] = mapped_column(Boolean, default=False)
    protocol: Mapped[str] = mapped_column(String(10), default="smtp")
    note: Mapped[str] = mapped_column(Text, default="")


class TlsPolicy(Base):
    __tablename__ = "tls_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination: Mapped[str] = mapped_column(String(260), unique=True)
    level: Mapped[str] = mapped_column(String(10), default="encrypt")
    note: Mapped[str] = mapped_column(Text, default="")


class OutboundHold(Base):
    """A paused sender (one IP address or SMTP account). While active, its mail is
    accepted but kept in Postfix's hold queue until an admin releases it."""

    __tablename__ = "outbound_hold"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # ip or account
    value: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(String(40))  # spam, volume, manual
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    automatic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    # After a release the detector leaves this sender alone until then.
    quiet_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
