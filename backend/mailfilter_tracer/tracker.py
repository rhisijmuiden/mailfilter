"""Combine log events into one record per message, with a status per recipient."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from . import explain
from .parser import Event, split_relay

# Receiving services that only accept mail from our own servers or SMTP accounts
OUTBOUND_LISTENERS = ("submission", "submissions", "pickup")
# Delivery transports: relay = to a customer's own mail server, smtp = out to the internet
INBOUND_TRANSPORTS = ("relay",)
OUTBOUND_TRANSPORTS = ("smtp",)


@dataclass
class Recipient:
    address: str
    status: str = "queued"  # queued, sent, deferred, bounced, expired, quarantined, held, rejected, trapped
    dsn: str = ""
    reply: str = ""
    relay_host: str = ""
    relay_ip: str | None = None
    transport: str = ""
    delay: float | None = None
    attempts: int = 0
    explain: str | None = None
    updated: datetime | None = None


@dataclass
class Message:
    queue_id: str
    first_seen: datetime
    last_seen: datetime
    direction: str | None = None  # inbound or outbound
    listener: str = ""
    client_host: str = ""
    client_ip: str | None = None
    sasl_user: str | None = None
    message_id: str | None = None
    sender: str | None = None
    size: int | None = None
    spam_score: float | None = None
    spam_action: str | None = None
    symbols: dict = field(default_factory=dict)
    symbol_options: dict = field(default_factory=dict)
    parent: str | None = None  # for bounces: the message this bounce is about
    is_bounce: bool = False
    done: bool = False
    recipients: dict[str, Recipient] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """Overall status: delivered, partly_delivered, deferred, bounced, quarantined, held, rejected or queued."""
        states = {r.status for r in self.recipients.values()}
        if not states:
            return "rejected" if self.done and not self.recipients else "queued"
        if states == {"sent"}:
            return "delivered"
        if "trapped" in states and states <= {"trapped", "sent"}:
            return "trapped"  # caught by the Mailtrap (test environment); never sent
        if "quarantined" in states:
            return "quarantined"
        if "held" in states:  # outbound sender paused by Mailfilter; not a spam verdict
            return "held"
        if states <= {"rejected"}:
            return "rejected"
        if "sent" in states and states & {"bounced", "expired", "rejected"}:
            return "partly_delivered"
        if states & {"bounced", "expired"} and not states & {"queued", "deferred"}:
            return "bounced"
        if "deferred" in states:
            return "deferred"
        return "queued"

    def rcpt(self, address: str) -> Recipient:
        address = address.lower()
        if address not in self.recipients:
            self.recipients[address] = Recipient(address)
        return self.recipients[address]


class Tracker:
    """Keeps messages that are still in flight. apply() returns the message that changed.

    loader(queue_id) may return a Message stored earlier (for example after a restart,
    when a deferred message is retried hours later).
    """

    def __init__(self, loader: Callable[[str], Message | None] | None = None):
        self.open: dict[str, Message] = {}
        self.loader = loader

    def _get(self, qid: str, ts: datetime, create: bool = True) -> Message | None:
        msg = self.open.get(qid)
        if msg is None and self.loader:
            msg = self.loader(qid)
        if msg is None and create:
            msg = Message(qid, ts, ts)
        if msg is not None:
            self.open[qid] = msg
            msg.last_seen = max(msg.last_seen, ts)
        return msg

    def forget(self, qid: str) -> None:
        self.open.pop(qid, None)

    def apply(self, ev: Event) -> Message | None:
        d = ev.data
        if ev.kind == "noqueue_reject":
            return self._noqueue(ev)
        if not ev.qid:
            return None
        msg = self._get(ev.qid, ev.ts)

        if ev.kind == "client":
            msg.listener = ev.service.split("/")[0]
            msg.client_host = d.get("host", "")
            msg.client_ip = d.get("ip")
            msg.sasl_user = d.get("user")
            if msg.sasl_user or msg.listener in OUTBOUND_LISTENERS or msg.listener.startswith("port-"):
                msg.direction = "outbound"
        elif ev.kind == "pickup":
            msg.listener = "pickup"
            msg.client_host = "localhost"
            msg.direction = "outbound"
        elif ev.kind == "message_id":
            msg.message_id = d.get("mid") or None
        elif ev.kind == "from":
            msg.sender = d["sender"]
            msg.size = int(d["size"])
            if not msg.sender:
                msg.is_bounce = True
        elif ev.kind == "rspamd":
            msg.spam_score = d["score"]
            msg.spam_action = d["action"]
            msg.symbols = d["symbols"]
            msg.symbol_options = d.get("symbol_options", {})
            msg.message_id = msg.message_id or (d.get("mid") if d.get("mid") != "undef" else None)
            if d.get("user") and d["user"] != "unknown":
                msg.direction = "outbound"
        elif ev.kind == "hold":
            r = msg.rcpt(d["to"])
            r.status, r.reply, r.updated, r.explain = "quarantined", d["reply"], ev.ts, "quarantine"
            msg.sender = msg.sender if msg.sender is not None else d["sender"]
        elif ev.kind == "access_hold":
            r = msg.rcpt(d["to"])
            r.status, r.reply, r.updated, r.explain = "held", d["reply"], ev.ts, "outbound_paused"
            msg.sender = msg.sender if msg.sender is not None else d["sender"]
            msg.client_host, msg.client_ip = msg.client_host or d.get("host", ""), msg.client_ip or d.get("ip")
            msg.direction = "outbound"
        elif ev.kind == "milter_reject":
            r = msg.rcpt(d["to"])
            r.status, r.dsn, r.reply, r.updated = "rejected", d["dsn"], d["reply"], ev.ts
            r.explain = explain.explain_local(d["reply"], d["dsn"])
            msg.sender = msg.sender if msg.sender is not None else d["sender"]
            msg.client_host, msg.client_ip = msg.client_host or d.get("host", ""), msg.client_ip or d.get("ip")
        elif ev.kind == "delivery":
            r = msg.rcpt(d["to"])
            r.relay_host, r.relay_ip = split_relay(d["relay"])
            # Per-domain queues log as postfix/domain/<name>, throttled outbound ones as postfix/out/<name>
            parts = ev.service.split("/")
            r.transport = {"domain": "relay", "out": "smtp"}.get(parts[0], parts[-1])
            r.dsn, r.reply, r.updated = d["dsn"], d["reply"], ev.ts
            r.delay = float(d["delay"])
            r.attempts += 1
            r.status = {"sent": "sent", "deferred": "deferred", "bounced": "bounced",
                        "expired": "expired"}.get(d["status"], d["status"])
            r.explain = explain.explain_remote(r.dsn, r.reply)
            if r.relay_host == "mftrap" and r.status == "sent":
                # Stored in a Mailtrap mailbox instead of sent (see master.cf, mftrap pipe)
                r.status, r.explain, r.transport = "trapped", "mailtrap", "mailtrap"
                msg.direction = msg.direction or "outbound"
            if r.transport in INBOUND_TRANSPORTS:
                msg.direction = "inbound"
            elif r.transport in OUTBOUND_TRANSPORTS and msg.direction is None:
                msg.direction = "outbound"
        elif ev.kind == "expired":
            for r in msg.recipients.values():
                if r.status in ("queued", "deferred"):
                    r.status, r.updated, r.explain = "expired", ev.ts, "expired"
        elif ev.kind == "notification":
            child = self._get(d["child"], ev.ts)
            child.parent = msg.queue_id
            child.is_bounce = True
            child.direction = child.direction or "outbound"
        elif ev.kind == "removed":
            msg.done = True
        return msg

    def _noqueue(self, ev: Event) -> Message:
        """A message refused before it got a queue ID. Give it a stable made-up ID."""
        d = ev.data
        key = f"{ev.ts.isoformat()}|{d.get('ip')}|{d.get('sender')}|{d.get('to')}"
        qid = "R" + hashlib.sha1(key.encode()).hexdigest()[:15].upper()
        msg = Message(qid, ev.ts, ev.ts, listener=ev.service.split("/")[0],
                      client_host=d.get("host", ""), client_ip=d.get("ip"), sender=d.get("sender"))
        msg.direction = "outbound" if msg.listener in OUTBOUND_LISTENERS else "inbound"
        r = msg.rcpt(d.get("to", ""))
        r.status, r.dsn, r.reply, r.updated = "rejected", d["dsn"], d["reply"], ev.ts
        r.explain = explain.explain_local(d["reply"], d["dsn"])
        msg.done = True
        return msg
