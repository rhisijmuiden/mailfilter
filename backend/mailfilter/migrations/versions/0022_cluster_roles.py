"""Cluster: role per node (member, primary, standby) and what a peer reports about itself.

Revision ID: 0022_cluster_roles
Revises: 0021_link_clicks
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022_cluster_roles"
down_revision = "0021_link_clicks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cluster_node", sa.Column("role", sa.String(10), nullable=False, server_default="member"))
    op.add_column("cluster_node", sa.Column("info", JSONB))


def downgrade() -> None:
    op.drop_column("cluster_node", "info")
    op.drop_column("cluster_node", "role")
