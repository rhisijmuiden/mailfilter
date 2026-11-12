"""Monthly report per customer: the numbers per domain and month, and who wants the e-mail."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MonthlyStats(Base):
    """Mail counts for one domain in one month, kept after the trace data is gone.

    Written once a month for the month that just ended (see mailfilter.monthly).
    The domain is stored by name, so the history survives a removed domain."""

    __tablename__ = "monthly_stats"
    __table_args__ = (UniqueConstraint("month", "domain", name="uq_monthly_stats_month_domain"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)  # first day of the month
    domain: Mapped[str] = mapped_column(String(253), index=True)
    received: Mapped[int] = mapped_column(Integer, default=0)  # inbound messages
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    spam: Mapped[int] = mapped_column(Integer, default=0)  # held or refused as spam (no virus)
    virus: Mapped[int] = mapped_column(Integer, default=0)
    refused: Mapped[int] = mapped_column(Integer, default=0)  # e.g. unknown recipient
    other: Mapped[int] = mapped_column(Integer, default=0)  # still waiting or bounced
    outbound: Mapped[int] = mapped_column(Integer, default=0)  # sent by the domain
    outbound_blocked: Mapped[int] = mapped_column(Integer, default=0)  # outgoing spam/virus stopped
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonthlyReportUser(Base):
    """Per-user switch for the monthly e-mail (no row = on) and the last month sent."""

    __tablename__ = "monthly_report_user"
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
