"""Relay domains (outbound only) next to transport domains, the sender lock,
and SMTP accounts made in the web interface.

Revision ID: 0012_relay_domains
Revises: 0011_2fa_audit
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_relay_domains"
down_revision = "0011_2fa_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # transport: receives mail and delivers it to a mail server; relay: outbound only.
    op.add_column("domain_route", sa.Column("kind", sa.String(10), nullable=False, server_default="transport"))
    op.add_column("domain_route", sa.Column("sender_lock", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_table(
        "smtp_account",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("domain_id", sa.Integer, sa.ForeignKey("domain.id", ondelete="CASCADE"), nullable=False, index=True),
        # Dovecot format, e.g. {SSHA512}...; the password itself is only shown once.
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("smtp_account")
    op.drop_column("domain_route", "sender_lock")
    op.drop_column("domain_route", "kind")
