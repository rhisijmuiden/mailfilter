"""Mail trace tables, filled by the log collector (mailfilter_tracer).

Frozen copy of mailfilter_tracer.store.SCHEMA at the time of writing; it uses
IF NOT EXISTS, so servers where the collector already created the tables are
fine. Later changes to these tables go through new migrations.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA = """CREATE TABLE IF NOT EXISTS trace_message (
    queue_id     text PRIMARY KEY,
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    direction    text,
    status       text NOT NULL,
    listener     text,
    client_host  text,
    client_ip    inet,
    sasl_user    text,
    message_id   text,
    sender       text,
    size         bigint,
    spam_score   real,
    spam_action  text,
    symbols      jsonb NOT NULL DEFAULT '{}',
    parent       text,
    is_bounce    boolean NOT NULL DEFAULT false,
    done         boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS trace_message_first_seen ON trace_message (first_seen DESC);
CREATE INDEX IF NOT EXISTS trace_message_sender ON trace_message (lower(sender));
CREATE INDEX IF NOT EXISTS trace_message_message_id ON trace_message (message_id);
CREATE INDEX IF NOT EXISTS trace_message_status ON trace_message (status, first_seen DESC);

CREATE TABLE IF NOT EXISTS trace_recipient (
    queue_id    text NOT NULL REFERENCES trace_message (queue_id) ON DELETE CASCADE,
    address     text NOT NULL,
    status      text NOT NULL,
    dsn         text,
    reply       text,
    relay_host  text,
    relay_ip    inet,
    transport   text,
    delay       real,
    attempts    integer NOT NULL DEFAULT 0,
    explain     text,
    updated     timestamptz,
    PRIMARY KEY (queue_id, address)
);
CREATE INDEX IF NOT EXISTS trace_recipient_address ON trace_recipient (address);

CREATE TABLE IF NOT EXISTS tracer_state (
    key   text PRIMARY KEY,
    value text NOT NULL
);
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trace_recipient, trace_message, tracer_state")
