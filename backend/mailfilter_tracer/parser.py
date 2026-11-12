"""Turn single Postfix and Rspamd log lines into structured events.

Pure functions only, so everything here is easy to test with sample lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

QID = r"(?P<qid>[0-9A-Za-z]{8,20})"
HOSTIP = r"(?P<host>[^\[\s]*)\[(?P<ip>[^\]]+)\]"


@dataclass
class Event:
    kind: str
    ts: datetime
    service: str
    qid: str | None = None
    data: dict = field(default_factory=dict)


# Postfix: the part after "postfix/<service>[pid]: "
_POSTFIX = [
    ("client", re.compile(
        QID + r": client=" + HOSTIP + r"(?::\d+)?"
        r"(?:, sasl_method=[^,]+, sasl_username=(?P<user>[^,\s]+))?")),
    ("pickup", re.compile(QID + r": uid=(?P<uid>\d+) from=<(?P<sender>[^>]*)>")),
    ("message_id", re.compile(QID + r": (?:resent-)?message-id=<?(?P<mid>[^>]*)>?$")),
    ("hold", re.compile(
        QID + r": milter-hold: END-OF-MESSAGE from [^:]+: (?P<reply>.*?);"
        r" from=<(?P<sender>[^>]*)> to=<(?P<to>[^>]*)>")),
    # Postfix access-map HOLD (no milter- prefix): Mailfilter pausing a relay IP or SMTP account
    ("access_hold", re.compile(
        QID + r": hold: END-OF-MESSAGE from " + HOSTIP + r": (?P<reply>.*?);"
        r" from=<(?P<sender>[^>]*)> to=<(?P<to>[^>]*)>")),
    ("milter_reject", re.compile(
        QID + r": milter-reject: (?:END-OF-MESSAGE|RCPT|MAIL|DATA) from " + HOSTIP + r": "
        r"(?P<code>\d{3}) (?P<dsn>\d\.\d{1,3}\.\d{1,3}) (?P<reply>.*?);"
        r" from=<(?P<sender>[^>]*)> to=<(?P<to>[^>]*)>")),
    ("expired", re.compile(QID + r": from=<(?P<sender>[^>]*)>, status=expired, (?P<reply>.*)$")),
    ("from", re.compile(QID + r": from=<(?P<sender>[^>]*)>, size=(?P<size>\d+), nrcpt=(?P<nrcpt>\d+)")),
    ("delivery", re.compile(
        QID + r": to=<(?P<to>[^>]*)>,(?: orig_to=<[^>]*>,)? relay=(?P<relay>[^,]+),"
        r"(?: conn_use=\d+,)? delay=(?P<delay>[\d.]+), delays=[^,]+,"
        r" dsn=(?P<dsn>\d\.\d{1,3}\.\d{1,3}), status=(?P<status>\w+) \((?P<reply>.*)\)$")),
    ("notification", re.compile(
        QID + r": sender (?P<what>non-delivery|delay|delivery status) notification: (?P<child>[0-9A-Za-z]+)")),
    ("removed", re.compile(QID + r": removed$")),
    ("noqueue_reject", re.compile(
        r"NOQUEUE: (?:reject|milter-reject): (?:RCPT|MAIL|DATA|CONNECT|END-OF-MESSAGE) from "
        r"(?:" + HOSTIP + r"|\[(?P<pip>[^\]]+)\]:\d+): "
        r"(?P<code>\d{3}) (?P<dsn>\d\.\d{1,3}\.\d{1,3}) (?P<reply>.*?);"
        r" from=<(?P<sender>[^>]*)>,? to=<(?P<to>[^>]*)>")),
]

_RSPAMD = re.compile(
    r"rspamd_task_write_log: id: <(?P<mid>[^>]*)>, qid: <(?P<qid>[^>]*)>, ip: (?P<ip>[^,]+),"
    r"(?: user: (?P<user>[^,]+),)?(?: mid: [^,]*,)? from: <(?P<sender>[^>]*)>,"
    r" \((?P<setting>[\w-]+): (?P<spam>[FTS]) \((?P<action>[^)]+)\):"
    r" \[(?P<score>-?[\d.]+)/(?P<required>-?[\d.]+)\] \[(?P<symbols>.*?)\]\)")
_SYMBOL = re.compile(r"([A-Z][A-Z0-9_]+)\((-?[\d.]+)\)")

_SYSLOG = re.compile(
    r"^(?:(?P<iso>\d{4}-\d\d-\d\dT\S+)|(?P<bsd>\w{3} [ \d]\d \d\d:\d\d:\d\d)) \S+ "
    r"(?P<ident>[\w./-]+)(?:\[\d+\])?: (?P<msg>.*)$")


def parse_message(ident: str, message: str, ts: datetime) -> Event | None:
    """Parse one log message. ident is the syslog identifier, e.g. postfix/smtp."""
    ident = ident or ""
    if ident.startswith("postfix"):
        service = ident.split("/", 1)[1] if "/" in ident else ident
        for kind, rx in _POSTFIX:
            m = rx.search(message)
            if m:
                data = {k: v for k, v in m.groupdict().items() if v is not None}
                qid = data.pop("qid", None)
                if kind == "noqueue_reject" and "pip" in data:
                    data["ip"] = data.pop("pip")
                    data.setdefault("host", "")
                return Event(kind, ts, service, qid, data)
        return None
    if ident.startswith("rspamd"):
        m = _RSPAMD.search(message)
        if not m:
            return None
        d = m.groupdict()
        qid = d.pop("qid") or None
        if qid in ("undef", "-"):
            qid = None
        d["score"] = float(d["score"])
        d["required"] = float(d["required"])
        d["symbols"] = {name: float(score) for name, score in _SYMBOL.findall(d["symbols"])}
        return Event("rspamd", ts, "rspamd", qid, {k: v for k, v in d.items() if v is not None})
    return None


def parse_journal(entry: dict) -> Event | None:
    """Parse one entry of `journalctl -o json`."""
    msg = entry.get("MESSAGE")
    if not isinstance(msg, str):
        return None
    us = int(entry.get("__REALTIME_TIMESTAMP", 0))
    ts = datetime.fromtimestamp(us / 1_000_000, tz=UTC)
    return parse_message(entry.get("SYSLOG_IDENTIFIER", ""), msg, ts)


def parse_syslog(line: str, year: int | None = None) -> Event | None:
    """Parse a classic syslog line (handy for tests and for importing old /var/log/mail.log)."""
    m = _SYSLOG.match(line.rstrip("\n"))
    if not m:
        return None
    if m["iso"]:
        ts = datetime.fromisoformat(m["iso"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    else:
        y = year or datetime.now(UTC).year
        ts = datetime.strptime(f"{y} {m['bsd']}", "%Y %b %d %H:%M:%S").replace(tzinfo=UTC)
    return parse_message(m["ident"], m["msg"], ts)


def split_relay(relay: str) -> tuple[str, str | None]:
    """'mx.example.com[192.0.2.1]:25' -> ('mx.example.com', '192.0.2.1'). 'none' -> ('', None)."""
    m = re.match(r"([^\[]*)\[([^\]]+)\](?::\d+)?$", relay.strip())
    if m:
        return m.group(1), m.group(2)
    return ("" if relay.strip() == "none" else relay.strip()), None
