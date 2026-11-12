"""Mail archive: an unchangeable, searchable copy of all mail per domain.

The messages themselves are gzip files on disk (see mailfilter.archive.store);
these tables hold what is needed to find them and to prove nothing changed:

- ArchiveDomain: per domain on/off, retention and legal hold, plus the chain head.
- ArchiveMessage: one row per archived message, linked into a per-domain hash chain.
- ArchiveSeal: one signed seal per domain and day (the chain head of that day).
- ArchivePurge: one row per retention clean-up, so the chain stays verifiable.

Database triggers (migration 0017_archive) refuse changes to sealed rows, deletes
before the retention date or under a legal hold, and any change to seals and purges.
Domains are stored by name, so the archive survives a removed domain.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ArchiveDomain(Base):
    __tablename__ = "archive_domain"
    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_years: Mapped[int] = mapped_column(Integer, default=7)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    legal_hold_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    legal_hold_note: Mapped[str] = mapped_column(String(300), default="")
    # Chain state: the last sequence number and its entry hash.
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    head: Mapped[str] = mapped_column(String(64), default="0" * 64)
    # Highest retain_until so far: retention never gets shorter for later mail, so
    # clean-ups always remove the oldest part of the chain.
    retain_floor: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArchiveMessage(Base):
    __tablename__ = "archive_message"
    __table_args__ = (UniqueConstraint("domain", "seq", name="uq_archive_message_domain_seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    queue_id: Mapped[str] = mapped_column(String(40), default="")
    sender: Mapped[str] = mapped_column(Text, default="")  # envelope sender
    from_addr: Mapped[str] = mapped_column(Text, default="")  # From: header
    subject: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[str] = mapped_column(Text, default="")
    header_to: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # To + Cc
    envelope_to: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # real recipients, incl. Bcc
    envelope_done: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list] = mapped_column(JSONB, default=list)  # [{name, type, size}]
    size: Mapped[int] = mapped_column(BigInteger)  # original size
    stored_size: Mapped[int] = mapped_column(BigInteger)  # on disk (gzip)
    sha256: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))
    retain_until: Mapped[date] = mapped_column(Date, index=True)
    path: Mapped[str] = mapped_column(Text)  # relative to the archive folder
    search: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    sealed: Mapped[bool] = mapped_column(Boolean, default=False)


class ArchiveSeal(Base):
    __tablename__ = "archive_seal"
    __table_args__ = (UniqueConstraint("domain", "day", name="uq_archive_seal_domain_day"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    day: Mapped[date] = mapped_column(Date)
    first_seq: Mapped[int] = mapped_column(BigInteger)
    last_seq: Mapped[int] = mapped_column(BigInteger)
    count: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)
    chain_head: Mapped[str] = mapped_column(String(64))
    envelope_digest: Mapped[str] = mapped_column(String(64))
    prev_seal: Mapped[str] = mapped_column(String(64))
    seal_hash: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)  # Ed25519, hex
    key_id: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArchivePurge(Base):
    __tablename__ = "archive_purge"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    through_seq: Mapped[int] = mapped_column(BigInteger)
    last_hash: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)
    retain_until: Mapped[date] = mapped_column(Date)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
