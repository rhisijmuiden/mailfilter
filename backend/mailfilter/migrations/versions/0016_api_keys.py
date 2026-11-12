"""API keys for the public REST API.

Revision ID: 0016_api_keys
Revises: 0015_spam_feedback
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB

revision = "0016_api_keys"
down_revision = "0015_spam_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("hint", sa.String(20), nullable=False),
        sa.Column("scopes", JSONB, nullable=False, server_default="{}"),
        sa.Column("allowed_ips", JSONB, nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(320), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_ip", INET),
    )


def downgrade() -> None:
    op.drop_table("api_key")
