"""Paused senders (automatic outbound abuse protection).

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_hold",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
        sa.Column("automatic", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by", sa.Integer, sa.ForeignKey("app_user.id", ondelete="SET NULL")),
        sa.Column("quiet_until", sa.DateTime(timezone=True)),
    )
    # At most one active pause per sender
    op.create_index("uq_outbound_hold_active", "outbound_hold", ["kind", "value"], unique=True,
                    postgresql_where=sa.text("released_at IS NULL"))


def downgrade() -> None:
    op.drop_table("outbound_hold")
