"""Mailtrap mailboxes for test environments.

Revision ID: 0013_mailtrap
Revises: 0012_relay_domains
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_mailtrap"
down_revision = "0012_relay_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mailtrap",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False, server_default=""),
        sa.Column("domain_id", sa.Integer, sa.ForeignKey("domain.id", ondelete="SET NULL"), nullable=True, index=True),
        # IMAP/SMTP login and the name of the mailbox directory
        sa.Column("login", sa.String(100), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False, server_default=""),
        sa.Column("smtp_login", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("imap", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("mode", sa.String(10), nullable=False, server_default="all"),
        sa.Column("allow", JSONB, nullable=False, server_default="[]"),
        sa.Column("retention_days", sa.Integer, nullable=False, server_default="7"),
        sa.Column("max_messages", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "mailtrap_network",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mailtrap_id", sa.Integer, sa.ForeignKey("mailtrap.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("cidr", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
    )
    op.create_table(
        "mailtrap_user",
        sa.Column("mailtrap_id", sa.Integer, sa.ForeignKey("mailtrap.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("mailtrap_user")
    op.drop_table("mailtrap_network")
    op.drop_table("mailtrap")
