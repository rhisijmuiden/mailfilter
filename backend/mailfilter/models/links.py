"""Link protection: every click on a protected link in incoming mail."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LinkClick(Base):
    __tablename__ = "link_click"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    url: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(253), index=True)
    # Where the recipient was sent (a shortener's destination when known).
    final_url: Mapped[str] = mapped_column(Text, default="")
    recipient: Mapped[str] = mapped_column(String(320), default="")
    # Recipient domains with link protection on, for the customer's view.
    domains: Mapped[list[str]] = mapped_column(ARRAY(String(253)), default=list)
    message_id: Mapped[str] = mapped_column(Text, default="")
    queue_id: Mapped[str] = mapped_column(String(40), default="")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    level: Mapped[str] = mapped_column(String(10), index=True)  # safe | warn | block
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    # Went on to the site after a warning.
    continued: Mapped[bool] = mapped_column(Boolean, default=False)
    continued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Security scanners and link previews open links too; they are listed, but marked.
    scanner: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
