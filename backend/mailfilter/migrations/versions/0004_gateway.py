"""Domains (routing, queues, limits), relay access, relay ports, throttling.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    fk_domain = lambda ondelete="CASCADE": sa.ForeignKey("domain.id", ondelete=ondelete)  # noqa: E731
    op.create_table(
        "domain_route",
        sa.Column("domain_id", sa.Integer, fk_domain(), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("tls", sa.String(10), nullable=False, server_default="may"),
        sa.Column("verify_recipients", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("concurrency", sa.Integer),
        sa.Column("rate_delay", sa.Integer),
        sa.Column("out_per_hour", sa.Integer),
        sa.Column("out_per_day", sa.Integer),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "domain_destination",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("domain_id", sa.Integer, fk_domain(), nullable=False, index=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="25"),
        sa.Column("mx", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("protocol", sa.String(10), nullable=False, server_default="smtp"),
    )
    op.create_table(
        "relay_network",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cidr", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("domain_id", sa.Integer, fk_domain("SET NULL"), index=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("allow_broad", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "relay_port",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("port", sa.Integer, nullable=False, unique=True),
        sa.Column("tls", sa.String(10), nullable=False, server_default="may"),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
    )
    op.create_table(
        "destination_policy",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False, unique=True),
        sa.Column("concurrency", sa.Integer),
        sa.Column("rate_delay", sa.Integer),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "address_route",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("address", sa.String(320), nullable=False, unique=True),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="25"),
        sa.Column("mx", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("protocol", sa.String(10), nullable=False, server_default="smtp"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "tls_policy",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("destination", sa.String(260), nullable=False, unique=True),
        sa.Column("level", sa.String(10), nullable=False, server_default="encrypt"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )


def downgrade() -> None:
    for t in ("tls_policy", "address_route", "destination_policy", "relay_port", "relay_network",
              "domain_destination", "domain_route"):
        op.drop_table(t)
