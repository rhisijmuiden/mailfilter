"""Cluster nodes and request nonces (config sync between Mailfilter servers).

Revision ID: 0007_cluster
Revises: 0006_dmarc
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_cluster"
down_revision = "0006_dmarc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cluster_node",
        sa.Column("node_id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("url", sa.String(300), nullable=False),
        sa.Column("addresses", JSONB, nullable=False, server_default="[]"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_self", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text, nullable=False, server_default=""),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.String(40), nullable=False, server_default=""),
        sa.Column("revision", JSONB),
        sa.Column("latency_ms", sa.Integer),
    )
    op.create_table(
        "cluster_nonce",
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cluster_nonce_seen_at", "cluster_nonce", ["seen_at"])


def downgrade() -> None:
    op.drop_table("cluster_nonce")
    op.drop_table("cluster_node")
