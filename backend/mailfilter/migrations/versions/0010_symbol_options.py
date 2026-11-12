"""What each spam rule found (domains in links, SPF record, file names ...),
shown in the spam check report of the mail trace.

The log collector adds the same column itself (IF NOT EXISTS), whichever runs first.

Revision ID: 0010_symbol_options
Revises: 0009_trace_indexes
"""
from alembic import op

revision = "0010_symbol_options"
down_revision = "0009_trace_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trace_message ADD COLUMN IF NOT EXISTS symbol_options jsonb NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE trace_message DROP COLUMN IF EXISTS symbol_options")
