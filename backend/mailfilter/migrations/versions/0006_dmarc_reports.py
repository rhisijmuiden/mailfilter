"""DMARC aggregate reports

Revision ID: 0006_dmarc
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_dmarc"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dmarc_report",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_name", sa.String(255), nullable=False),
        sa.Column("org_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("report_id", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False, index=True),
        sa.Column("begin", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("p", sa.String(12), nullable=False, server_default=""),
        sa.Column("sp", sa.String(12), nullable=False, server_default=""),
        sa.Column("pct", sa.Integer),
        sa.Column("adkim", sa.String(1), nullable=False, server_default="r"),
        sa.Column("aspf", sa.String(1), nullable=False, server_default="r"),
        sa.Column("messages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source", sa.String(10), nullable=False, server_default="mail"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_name", "report_id", "domain", name="uq_dmarc_report_key"),
    )
    op.create_table(
        "dmarc_row",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("report_pk", sa.Integer, sa.ForeignKey("dmarc_report.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_ip", sa.String(45), nullable=False, index=True),
        sa.Column("source_host", sa.String(253), nullable=False, server_default=""),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("disposition", sa.String(12), nullable=False, server_default="none"),
        sa.Column("dkim", sa.String(10), nullable=False, server_default=""),
        sa.Column("spf", sa.String(10), nullable=False, server_default=""),
        sa.Column("header_from", sa.String(253), nullable=False, server_default=""),
        sa.Column("envelope_from", sa.String(253), nullable=False, server_default=""),
        sa.Column("dkim_auth", JSONB, nullable=False, server_default="[]"),
        sa.Column("spf_auth", JSONB, nullable=False, server_default="[]"),
        sa.Column("reasons", JSONB, nullable=False, server_default="[]"),
    )
    op.create_table(
        "dmarc_ingest",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("source", sa.String(10), nullable=False, server_default="mail"),
        sa.Column("sender", sa.String(320), nullable=False, server_default=""),
        sa.Column("subject", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("domains", JSONB, nullable=False, server_default="[]"),
        sa.Column("reports", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("dmarc_ingest")
    op.drop_table("dmarc_row")
    op.drop_table("dmarc_report")
