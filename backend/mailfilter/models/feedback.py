"""Spam and not-spam reports that end users send in (Outlook's Report button,
or a mail forwarded to spam@ / notspam@ on the server)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SpamFeedback(Base):
    """One report: who reported which message as spam or as good mail, and
    what Mailfilter did with it."""

    __tablename__ = "spam_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    kind: Mapped[str] = mapped_column(String(4))  # spam | ham
    # learned | not_learned | unverified | not_found | not_customer | duplicate | rate_limited | disabled | no_message | undone
    status: Mapped[str] = mapped_column(String(16), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    # How the original arrived: attachment (forwarded as attachment / Report button) or inline (plain forward)
    source: Mapped[str] = mapped_column(String(10), default="attachment")
    reporter: Mapped[str] = mapped_column(String(320), index=True)
    domain: Mapped[str] = mapped_column(String(253), default="", index=True)  # the reporter's (hosted) domain
    verified: Mapped[str] = mapped_column(String(10), default="")  # dmarc | dkim | spf | "" (not proven)
    # The reported message, as found in the mail trace
    queue_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message_id: Mapped[str] = mapped_column(String(998), default="")
    sender: Mapped[str] = mapped_column(String(320), default="")
    recipient: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String(20), default="")
    learned: Mapped[str | None] = mapped_column(String(4), nullable=True)  # spam | ham | None
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # sender put on the reporter's block list
    # A copy of the reported message, kept a while so a report can be undone or learned later.
    raw: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    handled_by: Mapped[str] = mapped_column(String(320), default="")  # who undid / learned it by hand
