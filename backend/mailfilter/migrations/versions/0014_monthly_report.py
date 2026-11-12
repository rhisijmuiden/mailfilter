"""Monthly report per customer: counts per domain and month, per-user switch.

Revision ID: 0014_monthly_report
Revises: 0013_mailtrap
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_monthly_report"
down_revision = "0013_mailtrap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("month", sa.Date, nullable=False, index=True),
        sa.Column("domain", sa.String(253), nullable=False, index=True),
        *[sa.Column(c, sa.Integer, nullable=False, server_default="0")
          for c in ("received", "delivered", "spam", "virus", "refused", "other", "outbound", "outbound_blocked")],
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("month", "domain", name="uq_monthly_stats_month_domain"),
    )
    op.create_table(
        "monthly_report_user",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_month", sa.Date, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("monthly_report_user")
    op.drop_table("monthly_stats")
