"""Link protection: clicks on protected links.

Revision ID: 0021_link_clicks
Revises: 0020_recipient_check
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB

revision = "0021_link_clicks"
down_revision = "0020_recipient_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "link_click",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("final_url", sa.Text, nullable=False, server_default=""),
        sa.Column("recipient", sa.String(320), nullable=False, server_default=""),
        sa.Column("domains", ARRAY(sa.String(253)), nullable=False, server_default="{}"),
        sa.Column("message_id", sa.Text, nullable=False, server_default=""),
        sa.Column("queue_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("continued", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("continued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scanner", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ip", INET, nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=False, server_default=""),
    )
    op.create_index("ix_link_click_at", "link_click", ["at"])
    op.create_index("ix_link_click_host", "link_click", ["host"])
    op.create_index("ix_link_click_level", "link_click", ["level"])
    op.create_index("ix_link_click_domains", "link_click", ["domains"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("link_click")
