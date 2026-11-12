"""Cluster: the Mailfilter servers this one exchanges configuration with.

Membership (node_id, name, url, addresses) is the same on every node and is
part of the synced configuration. The other columns are what *this* node saw
of a peer the last time it talked to it, so they differ per node."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ClusterNode(Base):
    __tablename__ = "cluster_node"
    node_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(300))
    addresses: Mapped[list] = mapped_column(JSONB, default=list)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    # ---- local view of the peer (not synced) ----
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[str] = mapped_column(String(40), default="")
    revision: Mapped[Any] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)

    LOCAL = frozenset({"is_self", "last_seen_at", "last_sync_at", "last_error", "last_error_at", "version",
                       "revision", "latency_ms", "joined_at"})


class ClusterNonce(Base):
    """Nonces of requests from other nodes seen in the last minutes (replay protection)."""

    __tablename__ = "cluster_nonce"
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
