"""Store message records in PostgreSQL (psycopg 3)."""
from __future__ import annotations

import json

import psycopg

from .tracker import Message, Recipient

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_message (
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

-- Added later: what each spam rule found (shown in the spam check report).
ALTER TABLE trace_message ADD COLUMN IF NOT EXISTS symbol_options jsonb NOT NULL DEFAULT '{}';

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

_MSG_COLS = ("queue_id", "first_seen", "last_seen", "direction", "status", "listener", "client_host",
             "client_ip", "sasl_user", "message_id", "sender", "size", "spam_score", "spam_action",
             "symbols", "symbol_options", "parent", "is_bounce", "done")
_RCPT_COLS = ("queue_id", "address", "status", "dsn", "reply", "relay_host", "relay_ip", "transport",
              "delay", "attempts", "explain", "updated")


def _upsert(table: str, cols: tuple, keys: tuple) -> str:
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys and c != "first_seen")
    return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {sets}")


class Store:
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=False, client_encoding="utf8")

    def migrate(self) -> None:
        with self.conn.transaction():
            self.conn.execute(SCHEMA)

    def save(self, msgs: list[Message], cursor: str | None = None) -> None:
        """Write messages and the journal cursor in one transaction, so nothing is lost or doubled."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            for m in msgs:
                row = {c: getattr(m, c) for c in _MSG_COLS if c not in ("status", "symbols", "symbol_options")}
                row["status"] = m.status
                row["symbols"] = json.dumps(m.symbols)
                row["symbol_options"] = json.dumps(m.symbol_options)
                cur.execute(_upsert("trace_message", _MSG_COLS, ("queue_id",)),
                            [row[c] for c in _MSG_COLS])
                for r in m.recipients.values():
                    vals = {"queue_id": m.queue_id, **r.__dict__}
                    cur.execute(_upsert("trace_recipient", _RCPT_COLS, ("queue_id", "address")),
                                [vals[c] for c in _RCPT_COLS])
            if cursor:
                cur.execute("INSERT INTO tracer_state (key, value) VALUES ('journal_cursor', %s) "
                            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", [cursor])

    def cursor(self) -> str | None:
        row = self.conn.execute("SELECT value FROM tracer_state WHERE key = 'journal_cursor'").fetchone()
        self.conn.commit()
        return row[0] if row else None

    def load(self, queue_id: str) -> Message | None:
        """Bring back a message that is not finished yet (needed after a restart)."""
        cur = self.conn.execute(f"SELECT {', '.join(_MSG_COLS)} FROM trace_message WHERE queue_id = %s",
                                [queue_id])
        row = cur.fetchone()
        if not row:
            self.conn.commit()
            return None
        data = dict(zip(_MSG_COLS, row))
        data.pop("status")
        for k in ("client_ip",):
            data[k] = str(data[k]) if data[k] is not None else None
        msg = Message(**data)
        rows = self.conn.execute(f"SELECT {', '.join(_RCPT_COLS)} FROM trace_recipient WHERE queue_id = %s",
                                 [queue_id]).fetchall()
        self.conn.commit()
        for r in rows:
            rd = dict(zip(_RCPT_COLS, r))
            rd.pop("queue_id")
            rd["relay_ip"] = str(rd["relay_ip"]) if rd["relay_ip"] is not None else None
            msg.recipients[rd["address"]] = Recipient(**rd)
        return msg

    def prune(self, days: int) -> int:
        with self.conn.transaction():
            cur = self.conn.execute(
                "DELETE FROM trace_message WHERE last_seen < now() - make_interval(days => %s)", [days])
            return cur.rowcount
