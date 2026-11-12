"""Two-factor login (TOTP) and audit log filtering.

Revision ID: 0009_2fa_audit
Revises: 0008_cluster
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_2fa_audit"
down_revision = "0008_cluster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TOTP secret (Fernet-encrypted with APP_SECRET). totp_enabled_at is set once
    # the user confirmed a code; before that the secret is only a pending setup.
    op.add_column("app_user", sa.Column("totp_secret", sa.Text))
    op.add_column("app_user", sa.Column("totp_enabled_at", sa.DateTime(timezone=True)))
    # Last accepted 30-second step, so a code cannot be used twice.
    op.add_column("app_user", sa.Column("totp_last_step", sa.BigInteger))
    # SHA-256 hashes of the unused recovery codes.
    op.add_column("app_user", sa.Column("recovery_codes", JSONB, nullable=False, server_default="[]"))

    # Who did it stays readable after the user is deleted; the domain lets
    # customers see entries about their own domains.
    op.add_column("audit_log", sa.Column("user_email", sa.String(320)))
    op.add_column("audit_log", sa.Column("domain", sa.String(253)))
    op.create_index("ix_audit_log_domain", "audit_log", ["domain"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.execute("UPDATE audit_log a SET user_email = u.email FROM app_user u WHERE a.user_id = u.id")


def downgrade() -> None:
    op.drop_index("ix_audit_log_action", "audit_log")
    op.drop_index("ix_audit_log_user_id", "audit_log")
    op.drop_index("ix_audit_log_domain", "audit_log")
    op.drop_column("audit_log", "domain")
    op.drop_column("audit_log", "user_email")
    for c in ("recovery_codes", "totp_last_step", "totp_enabled_at", "totp_secret"):
        op.drop_column("app_user", c)
