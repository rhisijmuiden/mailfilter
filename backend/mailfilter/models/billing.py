"""Usage per customer as a basis for invoices: who pays for which domain
(billing customers, optionally through a reseller) and the measured usage.

A billing customer is an organisation, not a login: a company with three
logins is one customer. Domains point at their billing customer
(Domain.billing_customer_id); a user with role "reseller" points at its
reseller (User.reseller_id).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class Reseller(Base):
    """A partner that sells Mailfilter to its own customers and gets one invoice for all of them."""

    __tablename__ = "billing_reseller"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    ref: Mapped[str] = mapped_column(String(100), default="")  # customer number in the accounting system
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BillingCustomer(Base):
    __tablename__ = "billing_customer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    ref: Mapped[str] = mapped_column(String(100), default="")
    reseller_id: Mapped[int | None] = mapped_column(ForeignKey("billing_reseller.id", ondelete="SET NULL"), nullable=True, index=True)
    # Prices that differ from the general tariffs, e.g. {"per_mailbox": 0.5} or {"fixed": 25}.
    tariffs: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reseller: Mapped[Reseller | None] = relationship(lazy="joined")


class UsageDaily(Base):
    """One measurement per domain and day: proves the domain existed that day and
    records what is in the archive (when the archive feature is installed)."""

    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("day", "domain", name="uq_usage_daily_day_domain"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    domain: Mapped[str] = mapped_column(String(253))
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # its billing customer that day
    archive_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    archive_messages: Mapped[int] = mapped_column(BigInteger, default=0)


class UsageMonth(Base):
    """The usage of one domain in a finished month, frozen together with who paid for it
    then. Mail counts live in monthly_stats (same month and domain)."""

    __tablename__ = "usage_month"
    __table_args__ = (UniqueConstraint("month", "domain", name="uq_usage_month_month_domain"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    domain: Mapped[str] = mapped_column(String(253))
    mailboxes: Mapped[int] = mapped_column(Integer, default=0)
    archive_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # highest daily value
    archive_messages: Mapped[int] = mapped_column(BigInteger, default=0)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # plain id: history survives a delete
    customer: Mapped[str] = mapped_column(String(200), default="")  # "" = no customer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageCustomerMonth(Base):
    """A billing customer as it was when a month was closed: reseller and the prices used."""

    __tablename__ = "usage_customer_month"
    __table_args__ = (UniqueConstraint("month", "customer", name="uq_usage_customer_month"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer: Mapped[str] = mapped_column(String(200))
    ref: Mapped[str] = mapped_column(String(100), default="")
    reseller_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reseller: Mapped[str] = mapped_column(String(200), default="")
    reseller_ref: Mapped[str] = mapped_column(String(100), default="")
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    tariffs: Mapped[dict] = mapped_column(JSONB, default=dict)  # the effective prices
