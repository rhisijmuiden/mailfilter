"""Spam / not-spam reports from end users.

Revision ID: 0015_spam_feedback
Revises: 0014_monthly_report
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_spam_feedback"
down_revision = "0014_monthly_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spam_feedback",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.String(4), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.String(10), nullable=False, server_default="attachment"),
        sa.Column("reporter", sa.String(320), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False, server_default=""),
        sa.Column("verified", sa.String(10), nullable=False, server_default=""),
        sa.Column("queue_id", sa.String(40), nullable=True),
        sa.Column("message_id", sa.String(998), nullable=False, server_default=""),
        sa.Column("sender", sa.String(320), nullable=False, server_default=""),
        sa.Column("recipient", sa.String(320), nullable=False, server_default=""),
        sa.Column("subject", sa.String(500), nullable=False, server_default=""),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("action", sa.String(20), nullable=False, server_default=""),
        sa.Column("learned", sa.String(4), nullable=True),
        sa.Column("blocked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("raw", sa.LargeBinary, nullable=True),
        sa.Column("handled_by", sa.String(320), nullable=False, server_default=""),
    )
    op.create_index("ix_spam_feedback_at", "spam_feedback", ["at"])
    op.create_index("ix_spam_feedback_status", "spam_feedback", ["status"])
    op.create_index("ix_spam_feedback_reporter", "spam_feedback", ["reporter"])
    op.create_index("ix_spam_feedback_domain", "spam_feedback", ["domain"])


def downgrade() -> None:
    op.drop_table("spam_feedback")
