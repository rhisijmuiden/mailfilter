"""Allow/block lists per mailbox or domain, daily quarantine report.

Revision ID: 0007
Revises: 0006_dmarc
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006_dmarc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sender_list_entry",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("list", sa.String(10), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("domain_id", sa.Integer, sa.ForeignKey("domain.id", ondelete="CASCADE"), index=True),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", sa.String(320), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("recipient", "sender", name="uq_sender_list_pair"),
    )
    op.create_table(
        "digest_domain",
        sa.Column("domain_id", sa.Integer, sa.ForeignKey("domain.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "digest_sent",
        sa.Column("recipient", sa.String(320), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("digest_sent")
    op.drop_table("digest_domain")
    op.drop_table("sender_list_entry")
