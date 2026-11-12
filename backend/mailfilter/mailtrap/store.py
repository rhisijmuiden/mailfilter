"""Mailtrap mailboxes on disk: one Maildir per mailbox.

The pipe adds a few X-Mailfilter-* header lines at the top of every message,
so the original envelope (who would have received it, which server sent it)
travels with the file and is also visible in a mail program over IMAP.

A message's id is its Maildir base name (the part before ":2,"), which stays
the same when the message is read (new/ -> cur/) or flagged, also by Dovecot.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser, BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

CONFIG = Path(os.environ.get("MAILFILTER_MAILTRAP_CONFIG", "/etc/mailfilter/mailtrap.json"))

H_TRAP = "X-Mailfilter-Trap"
H_FROM = "X-Mailfilter-Envelope-From"
H_TO = "X-Mailfilter-Envelope-To"
H_CLIENT = "X-Mailfilter-Client"
H_DELIVERED = "X-Mailfilter-Delivered-To"
H_SUMMARY = "X-Mailfilter-Trap-Summary"
OUR_HEADERS = (H_TRAP, H_FROM, H_TO, H_CLIENT, H_DELIVERED, H_SUMMARY)

ID_RE = re.compile(r"[A-Za-z0-9._,=-]{1,200}")
HEAD_BYTES = 128 * 1024


def load_config(path: Path | None = None) -> dict:
    """{"enabled": bool, "boxes": {slug: {...}}} as written by mailfilter-config."""
    try:
        return json.loads((path or CONFIG).read_text())
    except (OSError, ValueError):
        return {"enabled": False, "boxes": {}}


def _clean(value: str) -> str:
    return re.sub(r"[\r\n\t]+", " ", value or "").strip()[:2000]


def line_ending(raw: bytes) -> bytes:
    """Postfix hands the pipe LF line endings; keep whatever the message uses."""
    return b"\r\n" if raw.split(b"\n", 1)[0].endswith(b"\r") else b"\n"


def envelope_headers(slug: str, sender: str, recipients: list[str], client: str, sasl: str,
                     delivered: list[str], summary: str, nl: bytes = b"\n") -> bytes:
    lines = [f"{H_TRAP}: {_clean(slug)}",
             f"{H_FROM}: <{_clean(sender)}>",
             f"{H_TO}: {_clean(', '.join(recipients))}",
             f"{H_CLIENT}: {_clean(client)}" + (f" (login {_clean(sasl)})" if sasl else "")]
    if delivered:
        lines.append(f"{H_DELIVERED}: {_clean(', '.join(delivered))}")
    if summary:
        lines.append(f"{H_SUMMARY}: {_clean(summary)}")
    return nl.join(x.encode() for x in lines) + nl


def _uniq_name() -> str:
    now = time.time()
    host = re.sub(r"[^A-Za-z0-9-]", "-", socket.gethostname())[:60] or "mailfilter"
    return f"{int(now)}.M{int((now % 1) * 1_000_000)}P{os.getpid()}R{os.urandom(4).hex()}.{host}"


def write_message(maildir: Path, data: bytes) -> str:
    """Write a message the Maildir way (tmp/ then rename into new/). Returns its id."""
    for sub in ("tmp", "new", "cur"):
        (maildir / sub).mkdir(parents=True, exist_ok=True)
    name = _uniq_name()
    tmp = maildir / "tmp" / name
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, maildir / "new" / name)
    return name


# ---- reading ----------------------------------------------------------------------
@dataclass
class Entry:
    id: str
    path: Path
    seen: bool
    mtime: float
    size: int


def entries(maildir: Path) -> list[Entry]:
    """All messages, newest first."""
    out: list[Entry] = []
    for sub in ("new", "cur"):
        try:
            it = os.scandir(maildir / sub)
        except FileNotFoundError:
            continue
        with it:
            for e in it:
                if not e.is_file() or e.name.startswith("."):
                    continue
                base, _, flags = e.name.partition(":2,")
                try:
                    st = e.stat()
                except FileNotFoundError:
                    continue
                out.append(Entry(base, Path(e.path), sub == "cur" and "S" in flags, st.st_mtime, st.st_size))
    out.sort(key=lambda x: x.mtime, reverse=True)
    return out


def find(maildir: Path, msg_id: str) -> Entry | None:
    if not ID_RE.fullmatch(msg_id or ""):
        return None
    for e in entries(maildir):
        if e.id == msg_id:
            return e
    return None


def mark_seen(e: Entry) -> Entry:
    """Move to cur/ with the S flag, like a mail program does (IMAP sees it as read)."""
    if e.seen:
        return e
    base, _, flags = e.path.name.partition(":2,")
    new_flags = "".join(sorted(set(flags) | {"S"}))
    dest = e.path.parent.parent / "cur" / f"{base}:2,{new_flags}"
    try:
        os.rename(e.path, dest)
    except FileNotFoundError:
        return e
    return Entry(e.id, dest, True, e.mtime, e.size)


def delete(e: Entry) -> None:
    try:
        os.unlink(e.path)
    except FileNotFoundError:
        pass


def read_bytes(e: Entry) -> bytes:
    return e.path.read_bytes()


def parse(e: Entry) -> EmailMessage:
    with open(e.path, "rb") as fh:
        return BytesParser(policy=policy.default).parse(fh)  # type: ignore[return-value]


def parse_headers(e: Entry) -> EmailMessage:
    with open(e.path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
    return BytesHeaderParser(policy=policy.default).parsebytes(head)  # type: ignore[return-value]


def strip_our_headers(raw: bytes) -> bytes:
    """The message as the test environment sent it (for "send anyway" and downloads)."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        head, sep, body = raw.partition(b"\n\n")
    nl = b"\r\n" if b"\r\n" in head else b"\n"
    keep, skip = [], False
    for line in head.split(nl):
        if line[:1] in (b" ", b"\t"):
            if not skip:
                keep.append(line)
            continue
        name = line.split(b":", 1)[0].decode("latin-1").strip().lower()
        skip = name in {h.lower() for h in OUR_HEADERS} or name == "return-path"
        if not skip:
            keep.append(line)
    return nl.join(keep) + sep + body


def addresses(value: str | None) -> list[str]:
    return [a for _, a in getaddresses([value or ""]) if a]


def header_date(msg: EmailMessage) -> float | None:
    try:
        return parsedate_to_datetime(str(msg["Date"])).timestamp() if msg["Date"] else None
    except (TypeError, ValueError, IndexError):
        return None


# ---- retention --------------------------------------------------------------------
@dataclass
class Cleanup:
    removed: int = 0
    kept: int = 0
    errors: list[str] = field(default_factory=list)


def cleanup(maildir: Path, retention_days: int, max_messages: int, now: float | None = None) -> Cleanup:
    """Remove messages older than the retention, then the oldest above the maximum."""
    now = now or time.time()
    res = Cleanup()
    items = entries(maildir)
    limit = now - int(retention_days) * 86400
    keep = []
    for e in items:
        if e.mtime < limit:
            delete(e)
            res.removed += 1
        else:
            keep.append(e)
    for e in keep[int(max_messages):]:
        delete(e)
        res.removed += 1
    res.kept = min(len(keep), int(max_messages))
    for f in (maildir / "tmp").glob("*") if (maildir / "tmp").exists() else []:
        try:
            if f.stat().st_mtime < now - 36 * 3600:  # Maildir rule: leftovers older than 36 hours
                f.unlink()
        except OSError:
            pass
    return res
