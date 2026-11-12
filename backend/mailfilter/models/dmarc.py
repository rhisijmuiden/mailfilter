"""DMARC aggregate (rua) reports that other mail providers send about our domains."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class DmarcReport(Base):
    """One aggregate report: one reporter (e.g. Google), one domain, one period."""

    __tablename__ = "dmarc_report"
    __table_args__ = (UniqueConstraint("org_name", "report_id", "domain", name="uq_dmarc_report_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_name: Mapped[str] = mapped_column(String(255))
    org_email: Mapped[str] = mapped_column(String(320), default="")
    report_id: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(253), index=True)  # the policy domain
    begin: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The DMARC record as the reporter saw it
    p: Mapped[str] = mapped_column(String(12), default="")
    sp: Mapped[str] = mapped_column(String(12), default="")
    pct: Mapped[int | None] = mapped_column(Integer)
    adkim: Mapped[str] = mapped_column(String(1), default="r")
    aspf: Mapped[str] = mapped_column(String(1), default="r")
    messages: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(10), default="mail")  # mail or upload
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rows: Mapped[list[DmarcRow]] = relationship(back_populates="report", cascade="all, delete-orphan",
                                                passive_deletes=True, lazy="selectin")


class DmarcRow(Base):
    """One line of a report: this many messages from this IP, with these results."""

    __tablename__ = "dmarc_row"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_pk: Mapped[int] = mapped_column(ForeignKey("dmarc_report.id", ondelete="CASCADE"), index=True)
    source_ip: Mapped[str] = mapped_column(String(45), index=True)
    source_host: Mapped[str] = mapped_column(String(253), default="")  # reverse DNS at the time of receipt
    count: Mapped[int] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(String(12), default="none")  # none, quarantine, reject
    dkim: Mapped[str] = mapped_column(String(10), default="")  # aligned DKIM result: pass / fail
    spf: Mapped[str] = mapped_column(String(10), default="")  # aligned SPF result: pass / fail
    header_from: Mapped[str] = mapped_column(String(253), default="")
    envelope_from: Mapped[str] = mapped_column(String(253), default="")
    # [{"domain", "selector", "result"}] and [{"domain", "scope", "result"}]
    dkim_auth: Mapped[list] = mapped_column(JSONB, default=list)
    spf_auth: Mapped[list] = mapped_column(JSONB, default=list)
    reasons: Mapped[list] = mapped_column(JSONB, default=list)  # [{"type", "comment"}], e.g. forwarded
    report: Mapped[DmarcReport] = relationship(back_populates="rows")


class DmarcIngest(Base):
    """What happened to each report mail or upload, so problems are visible in the UI."""

    __tablename__ = "dmarc_ingest"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    source: Mapped[str] = mapped_column(String(10), default="mail")
    sender: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12))  # ok, duplicate, ignored, error
    detail: Mapped[str] = mapped_column(Text, default="")
    domains: Mapped[list] = mapped_column(JSONB, default=list)
    reports: Mapped[int] = mapped_column(Integer, default=0)
