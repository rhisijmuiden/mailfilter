"""Recipient check per transport domain: ask the mail server, LDAP / Active
Directory, a fixed list, or no check.

Revision ID: 0015_recipient_check
Revises: 0014_monthly_report
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_recipient_check"
down_revision = "0014_monthly_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("domain_route", sa.Column("recipient_check", sa.String(10), nullable=False, server_default="callout"))
    op.execute("UPDATE domain_route SET recipient_check = 'off' WHERE NOT verify_recipients")
    # LDAP settings without the password; that one is encrypted with APP_SECRET.
    op.add_column("domain_route", sa.Column("recipient_ldap", JSONB, nullable=True))
    op.add_column("domain_route", sa.Column("recipient_ldap_password", sa.Text, nullable=True))
    op.add_column("domain_route", sa.Column("recipient_list", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    for c in ("recipient_list", "recipient_ldap_password", "recipient_ldap", "recipient_check"):
        op.drop_column("domain_route", c)
