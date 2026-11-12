"""Usage per customer for invoices: billing customers, resellers, measured usage.

Revision ID: 0016_usage_billing
Revises: 0015_spam_feedback
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_usage_billing"
down_revision = "0015_spam_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_reseller",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "billing_customer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("reseller_id", sa.Integer, sa.ForeignKey("billing_reseller.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("tariffs", JSONB, nullable=False, server_default="{}"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("domain", sa.Column("billing_customer_id", sa.Integer,
                                      sa.ForeignKey("billing_customer.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_domain_billing_customer_id", "domain", ["billing_customer_id"])
    op.add_column("app_user", sa.Column("reseller_id", sa.Integer,
                                        sa.ForeignKey("billing_reseller.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_app_user_reseller_id", "app_user", ["reseller_id"])
    op.create_table(
        "usage_daily",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("customer_id", sa.Integer, nullable=True),
        sa.Column("archive_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("archive_messages", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("day", "domain", name="uq_usage_daily_day_domain"),
    )
    op.create_table(
        "usage_month",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("month", sa.Date, nullable=False, index=True),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("mailboxes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("archive_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("archive_messages", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("customer_id", sa.Integer, nullable=True),
        sa.Column("customer", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("month", "domain", name="uq_usage_month_month_domain"),
    )
    op.create_table(
        "usage_customer_month",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("month", sa.Date, nullable=False, index=True),
        sa.Column("customer_id", sa.Integer, nullable=True),
        sa.Column("customer", sa.String(200), nullable=False),
        sa.Column("ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("reseller_id", sa.Integer, nullable=True),
        sa.Column("reseller", sa.String(200), nullable=False, server_default=""),
        sa.Column("reseller_ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("tariffs", JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("month", "customer", name="uq_usage_customer_month"),
    )


def downgrade() -> None:
    op.drop_table("usage_customer_month")
    op.drop_table("usage_month")
    op.drop_table("usage_daily")
    op.drop_index("ix_app_user_reseller_id", "app_user")
    op.drop_column("app_user", "reseller_id")
    op.drop_index("ix_domain_billing_customer_id", "domain")
    op.drop_column("domain", "billing_customer_id")
    op.drop_table("billing_customer")
    op.drop_table("billing_reseller")
