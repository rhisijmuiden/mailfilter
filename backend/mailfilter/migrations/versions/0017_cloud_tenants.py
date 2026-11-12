"""Cloud mail: Microsoft 365 / Google Workspace tenants behind the gateway.

Revision ID: 0017_cloud_tenants
Revises: 0016_usage_billing
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017_cloud_tenants"
down_revision = "0016_usage_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cloud_tenant",
        sa.Column("domain_id", sa.Integer, sa.ForeignKey("domain.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("steps", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("cloud_tenant")
