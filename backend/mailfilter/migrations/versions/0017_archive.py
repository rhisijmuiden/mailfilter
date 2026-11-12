"""Mail archive: domains, messages (hash chain), daily seals and clean-up log.

Triggers make the archive tamper-resistant in the database itself:
- archive_message: only the envelope recipients and the sealed flag may change,
  and only before the day is sealed; a row can be deleted only after its
  retention date and when its domain has no legal hold.
- archive_seal and archive_purge: rows can never be changed or deleted.

Revision ID: 0017_archive
Revises: 0016_usage_billing
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0017_archive"
down_revision = "0016_usage_billing"
branch_labels = None
depends_on = None

GUARD_MESSAGE = """
CREATE OR REPLACE FUNCTION archive_message_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.retain_until >= current_date THEN
      RAISE EXCEPTION 'archive_immutable: % #% is kept until %', OLD.domain, OLD.seq, OLD.retain_until;
    END IF;
    IF EXISTS (SELECT 1 FROM archive_domain d WHERE d.name = OLD.domain AND d.legal_hold) THEN
      RAISE EXCEPTION 'archive_immutable: % is under a legal hold', OLD.domain;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.sealed THEN
    RAISE EXCEPTION 'archive_immutable: % #% is sealed', OLD.domain, OLD.seq;
  END IF;
  IF (NEW.id, NEW.domain, NEW.seq, NEW.received_at, NEW.direction, NEW.queue_id, NEW.sender, NEW.from_addr,
      NEW.subject, NEW.message_id, NEW.header_to, NEW.attachments, NEW.size, NEW.stored_size, NEW.sha256,
      NEW.prev_hash, NEW.entry_hash, NEW.retain_until, NEW.path)
     IS DISTINCT FROM
     (OLD.id, OLD.domain, OLD.seq, OLD.received_at, OLD.direction, OLD.queue_id, OLD.sender, OLD.from_addr,
      OLD.subject, OLD.message_id, OLD.header_to, OLD.attachments, OLD.size, OLD.stored_size, OLD.sha256,
      OLD.prev_hash, OLD.entry_hash, OLD.retain_until, OLD.path) THEN
    RAISE EXCEPTION 'archive_immutable: % #% cannot be changed', OLD.domain, OLD.seq;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

GUARD_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION archive_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'archive_immutable: % rows cannot be changed or deleted', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "archive_domain",
        sa.Column("name", sa.String(253), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("retention_years", sa.Integer, nullable=False, server_default="7"),
        sa.Column("legal_hold", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("legal_hold_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_hold_note", sa.String(300), nullable=False, server_default=""),
        sa.Column("last_seq", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("head", sa.String(64), nullable=False, server_default="0" * 64),
        sa.Column("retain_floor", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "archive_message",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False, index=True),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("queue_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("sender", sa.Text, nullable=False, server_default=""),
        sa.Column("from_addr", sa.Text, nullable=False, server_default=""),
        sa.Column("subject", sa.Text, nullable=False, server_default=""),
        sa.Column("message_id", sa.Text, nullable=False, server_default=""),
        sa.Column("header_to", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("envelope_to", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("envelope_done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("attachments", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("stored_size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("retain_until", sa.Date, nullable=False, index=True),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("search", pg.TSVECTOR, nullable=True),
        sa.Column("sealed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("domain", "seq", name="uq_archive_message_domain_seq"),
    )
    op.create_index("ix_archive_message_received", "archive_message", ["received_at"])
    op.create_index("ix_archive_message_queue", "archive_message", ["domain", "queue_id"])
    op.create_index("ix_archive_message_search", "archive_message", ["search"], postgresql_using="gin")
    op.create_index("ix_archive_message_envelope", "archive_message", ["envelope_to"], postgresql_using="gin")
    op.create_index("ix_archive_message_header_to", "archive_message", ["header_to"], postgresql_using="gin")
    op.create_table(
        "archive_seal",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("first_seq", sa.BigInteger, nullable=False),
        sa.Column("last_seq", sa.BigInteger, nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("bytes", sa.BigInteger, nullable=False),
        sa.Column("chain_head", sa.String(64), nullable=False),
        sa.Column("envelope_digest", sa.String(64), nullable=False),
        sa.Column("prev_seal", sa.String(64), nullable=False),
        sa.Column("seal_hash", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text, nullable=False),
        sa.Column("key_id", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("domain", "day", name="uq_archive_seal_domain_day"),
    )
    op.create_table(
        "archive_purge",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("domain", sa.String(253), nullable=False, index=True),
        sa.Column("through_seq", sa.BigInteger, nullable=False),
        sa.Column("last_hash", sa.String(64), nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("bytes", sa.BigInteger, nullable=False),
        sa.Column("retain_until", sa.Date, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(GUARD_MESSAGE)
    op.execute(GUARD_APPEND_ONLY)
    op.execute("CREATE TRIGGER archive_message_guard BEFORE UPDATE OR DELETE ON archive_message "
               "FOR EACH ROW EXECUTE FUNCTION archive_message_guard()")
    for t in ("archive_seal", "archive_purge"):
        op.execute(f"CREATE TRIGGER {t}_guard BEFORE UPDATE OR DELETE ON {t} "
                   "FOR EACH ROW EXECUTE FUNCTION archive_append_only()")


def downgrade() -> None:
    for t in ("archive_purge", "archive_seal", "archive_message", "archive_domain"):
        op.drop_table(t)
    op.execute("DROP FUNCTION IF EXISTS archive_message_guard()")
    op.execute("DROP FUNCTION IF EXISTS archive_append_only()")
