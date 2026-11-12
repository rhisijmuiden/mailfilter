"""Indexes that keep the mail trace, quarantine and dashboard fast on a busy server.

* trace_message.parent: the bounces of a message (opened on every trace detail)
* open quarantine: the quarantine page and the "held" counters poll this
* domain part of sender / recipient: customer accounts filter on their domains

Revision ID: 0009_trace_indexes
Revises: 0008_cluster
"""
from alembic import op

revision = "0009_trace_indexes"
down_revision = "0008_cluster"
branch_labels = None
depends_on = None

INDEXES = {
    "trace_message_parent": "trace_message (parent) WHERE parent IS NOT NULL",
    "trace_message_open_quarantine": "trace_message (first_seen DESC) WHERE status = 'quarantined' AND NOT done",
    "trace_message_sender_domain": "trace_message (lower(split_part(sender, '@', 2)), first_seen DESC)",
    "trace_recipient_domain": "trace_recipient (lower(split_part(address, '@', 2)), queue_id)",
}


def upgrade() -> None:
    for name, spec in INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")


def downgrade() -> None:
    for name in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
