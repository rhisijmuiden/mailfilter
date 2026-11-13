"""Archive files on disk and what we read from a message for searching.

Layout under the archive folder (default /var/lib/mailfilter-archive):

    <domain>/<retain-until YYYY-MM>/<received YYYY-MM-DD>/<seq>-<sha256 prefix>.eml.gz

Grouping by retention month lets the root helper remove a whole month at once
when its time is up (mailfilterctl archive purge), and lock finished days
against changes (mailfilterctl archive lock, chattr +i).
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from pathlib import Path

# Postfix adds <domain>@ARCHIVE_HOST as an extra (bcc) recipient for archived
# domains; transport_maps sends that host to the mfarchive pipe (main.cf).
ARCHIVE_HOST = "archive.mailfilter.invalid"
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
MAX_TEXT = 200_000  # characters of body text that go into the search index
MAX_BYTES = 60 * 1024 * 1024


def root() -> Path:
    """The archive folder (tests point this elsewhere)."""
    from ..settings import get_settings

    return get_settings().archive_dir


def is_archive_address(address: str | None) -> bool:
    return bool(address) and address.lower().rstrip(">").endswith("@" + ARCHIVE_HOST)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel_path(domain: str, retain_until: date, received: datetime, seq: int, digest: str) -> str:
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"bad domain {domain!r}")
    return f"{domain}/{retain_until:%Y-%m}/{received:%Y-%m-%d}/{seq:010d}-{digest[:16]}.eml.gz"


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise ValueError("path outside the archive")
    return p


def write(root: Path, rel: str, raw: bytes) -> int:
    """Write the message once (never overwrite); returns the size on disk."""
    path = _safe(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o440)
    try:
        os.link(tmp, path)  # fails when the file exists: nothing is ever overwritten
    finally:
        os.unlink(tmp)
    return path.stat().st_size


def read(root: Path, rel: str) -> bytes:
    with gzip.open(_safe(root, rel), "rb") as f:
        return f.read(MAX_BYTES + 1)


def exists(root: Path, rel: str) -> bool:
    try:
        return _safe(root, rel).is_file()
    except ValueError:
        return False


# ---- reading a message ------------------------------------------------------------
class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # broken HTML: whatever we got so far
        pass
    text = "".join(p.parts)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


@dataclass
class Meta:
    subject: str = ""
    from_addr: str = ""
    message_id: str = ""
    date: str = ""
    header_to: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    text: str = ""  # readable body text (plain text, or HTML turned into text)


def _header(msg: EmailMessage, name: str) -> str:
    try:
        v = msg.get(name)
        return str(v).strip() if v is not None else ""
    except Exception:
        raw = msg.get_all(name, failobj=[])
        return str(raw[0]).strip() if raw else ""


def parse(raw: bytes) -> Meta:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)  # type: ignore[assignment]
    m = Meta(subject=_header(msg, "Subject")[:1000], message_id=_header(msg, "Message-ID")[:500],
             date=_header(msg, "Date")[:100])
    m.from_addr = parseaddr(_header(msg, "From"))[1].lower()[:320]
    tos = []
    for name in ("To", "Cc"):
        for _, addr in getaddresses([str(v) for v in msg.get_all(name, failobj=[])]):
            if addr and "@" in addr:
                tos.append(addr.lower()[:320])
    m.header_to = list(dict.fromkeys(tos))[:500]
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disp = (part.get_content_disposition() or "").lower()
        ctype = part.get_content_type()
        if filename or disp == "attachment":
            try:
                size = len(part.get_payload(decode=True) or b"")
            except Exception:
                size = 0
            m.attachments.append({"name": (filename or "")[:255], "type": ctype[:100], "size": size})
            continue
        if ctype in ("text/plain", "text/html"):
            try:
                content = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                content = payload.decode("utf-8", errors="replace")
            (plain if ctype == "text/plain" else html).append(str(content))
    text = "\n".join(plain) if plain else "\n".join(html_to_text(h) for h in html)
    m.text = text[:MAX_TEXT]
    m.attachments = m.attachments[:200]
    return m


def search_text(meta: Meta, sender: str, envelope: list[str]) -> str:
    """What goes into the full-text index (Postgres 'simple' configuration)."""
    names = " ".join(a["name"] for a in meta.attachments if a.get("name"))
    # Addresses are indexed whole and split, so "devries", "webshop-devries.nl" and the full address all match.
    addrs = [a for a in {sender, meta.from_addr, *meta.header_to, *envelope} if a]
    split = " ".join(re.sub(r"[@.\-_+]", " ", a) for a in addrs)
    return "\n".join([meta.subject, " ".join(addrs), split, names, meta.text])[: MAX_TEXT + 20_000]
