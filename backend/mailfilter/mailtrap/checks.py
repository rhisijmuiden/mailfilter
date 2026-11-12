"""Checks on caught test mail: the mistakes a test environment is meant to catch.

Every check gives a stable code, a status (ok, warn, bad, info) and a few
values; the web interface turns those into sentences in English and Dutch.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from html.parser import HTMLParser
from urllib.parse import urlsplit

from . import store

GMAIL_CLIP = 102 * 1024  # Gmail cuts off HTML larger than this ("[Message clipped]")
BIG_MESSAGE = 10 * 1024 * 1024
SPAM_MARK = 5.0  # Rspamd add_header (actions.conf)
SPAM_REJECT = 15.0  # Rspamd reject
BULK_RECIPIENTS = 50

# Host names that only exist inside a test or development setup.
TEST_TLDS = {"local", "localhost", "test", "example", "invalid", "lan", "internal", "home", "corp", "localdomain"}
TEST_LABELS = re.compile(r"^(staging|stage|test|testing|dev|develop|development|acc|acceptatie|acceptance|uat|qa|"
                         r"preprod|pre-prod|sandbox|local)(\d+)?$")
PLACEHOLDERS = re.compile(r"\{\{.{0,80}?\}\}|\{%.{0,80}?%\}|%%[A-Za-z_][\w.]{0,40}%%|\$\{[A-Za-z_][\w.]{0,40}\}|"
                          r"\*\|[A-Z_]{2,40}\|\*|\[\[[A-Za-z_][\w.]{0,40}\]\]|\{\$[A-Za-z_][\w.]{0,40}\}")
URL_RE = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.I)
SCORE_RE = re.compile(r"score=(-?\d+(?:\.\d+)?)")


@dataclass
class Check:
    code: str
    status: str  # ok, warn, bad, info
    params: dict = field(default_factory=dict)


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images_without_alt: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self.links.append(a["href"].strip())
        if tag == "img":
            if "alt" not in a:  # alt="" is fine: that marks a decorative image
                self.images_without_alt.append(a.get("src", "")[:200])


def _is_test_host(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return False
    if not host:
        return False
    if port and port not in (80, 443):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) == 1 or labels[-1] in TEST_TLDS:
        return True
    return any(TEST_LABELS.match(lbl) for lbl in labels[:-1])


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[1].lower() if "@" in addr else ""


def _aligned(a: str, b: str) -> bool:
    """Relaxed DMARC alignment, roughly: one domain is the other or a subdomain of it."""
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def bodies(msg: EmailMessage) -> tuple[str | None, str | None, list[dict]]:
    """(html, text, attachments) of a message."""
    html = text = None
    attachments: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = part.get_content_disposition()
        if disp == "attachment" or (disp == "inline" and part.get_filename() and not ctype.startswith("text/")) \
                or (part.get_filename() and not ctype.startswith("text/") and not part.get("Content-ID")):
            payload = part.get_payload(decode=True) or b""
            attachments.append({"name": part.get_filename() or "", "type": ctype, "size": len(payload)})
            continue
        if ctype == "text/html" and html is None:
            html = _text(part)
        elif ctype == "text/plain" and text is None:
            text = _text(part)
    return html, text, attachments


def _text(part) -> str:
    try:
        return part.get_content()
    except (LookupError, ValueError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def spam_score(msg: EmailMessage) -> float | None:
    m = SCORE_RE.search(str(msg.get("X-Spam-Status", "")))
    return float(m.group(1)) if m else None


def run(msg: EmailMessage, size: int) -> list[Check]:
    out: list[Check] = []
    html, text, attachments = bodies(msg)

    score = spam_score(msg)
    if score is None:
        out.append(Check("spam_unknown", "info"))
    else:
        status = "ok" if score < SPAM_MARK else "warn" if score < SPAM_REJECT else "bad"
        out.append(Check("spam", status, {"score": round(score, 2), "mark": SPAM_MARK, "reject": SPAM_REJECT}))

    # Links that still point at the test environment
    links: list[str] = []
    if html:
        p = _Html()
        try:
            p.feed(html)
        except Exception:  # broken HTML must not break the checks
            pass
        links += [x for x in p.links if x.lower().startswith(("http://", "https://"))]
        missing_alt = p.images_without_alt
    else:
        missing_alt = []
    if text:
        links += URL_RE.findall(text)
    links = list(dict.fromkeys(x.rstrip(".,;") for x in links))
    test_links = [x for x in links if _is_test_host(x)]
    if test_links:
        out.append(Check("links_test", "bad", {"links": test_links[:10], "count": len(test_links)}))
    elif links:
        out.append(Check("links_ok", "ok", {"count": len(links)}))
    plain_http = [x for x in links if x.lower().startswith("http://") and x not in test_links]
    if plain_http:
        out.append(Check("links_http", "warn", {"links": plain_http[:10], "count": len(plain_http)}))

    # Unfilled template fields
    found = []
    for where in (str(msg.get("Subject", "")), html or "", text or ""):
        found += PLACEHOLDERS.findall(where)
    found = list(dict.fromkeys(found))
    if found:
        out.append(Check("template", "bad", {"fields": found[:10], "count": len(found)}))

    # DKIM signature (Rspamd signs mail of domains with a key) and alignment
    sig = str(msg.get("DKIM-Signature", ""))
    sender = (store.addresses(str(msg.get(store.H_FROM, ""))) or [""])[0]
    from_addr = (store.addresses(str(msg.get("From", ""))) or [""])[0]
    if sig:
        d = re.search(r"\bd=([^;\s]+)", sig)
        s = re.search(r"\bs=([^;\s]+)", sig)
        out.append(Check("dkim", "ok", {"domain": d.group(1) if d else "", "selector": s.group(1) if s else ""}))
    else:
        out.append(Check("dkim_missing", "warn", {"domain": _domain(from_addr)}))
    if not from_addr:
        out.append(Check("from_missing", "bad"))
    elif sender and not _aligned(_domain(sender), _domain(from_addr)):
        out.append(Check("alignment", "warn", {"from": _domain(from_addr), "sender": _domain(sender)}))
    elif sender:
        out.append(Check("alignment_ok", "ok", {"domain": _domain(from_addr)}))

    if html and text:
        out.append(Check("parts_ok", "ok"))
    elif html:
        out.append(Check("text_missing", "warn"))
    elif text:
        out.append(Check("text_only", "info"))
    else:
        out.append(Check("body_empty", "bad"))

    if missing_alt:
        out.append(Check("images_alt", "warn", {"count": len(missing_alt), "images": missing_alt[:5]}))

    html_size = len((html or "").encode("utf-8"))
    if html_size > GMAIL_CLIP:
        out.append(Check("gmail_clip", "warn", {"size": html_size, "limit": GMAIL_CLIP}))
    if size > BIG_MESSAGE:
        out.append(Check("size_big", "warn", {"size": size}))
    else:
        out.append(Check("size", "ok", {"size": size, "html": html_size}))

    if not str(msg.get("Subject", "")).strip():
        out.append(Check("subject_missing", "warn"))
    for h in ("Date", "Message-ID"):
        if not msg.get(h):
            out.append(Check("header_missing", "warn", {"header": h}))

    recipients = store.addresses(str(msg.get(store.H_TO, "")))
    bulk = bool(msg.get("List-Id")) or str(msg.get("Precedence", "")).lower() in ("bulk", "list") \
        or len(recipients) >= BULK_RECIPIENTS
    if bulk:
        if msg.get("List-Unsubscribe") and msg.get("List-Unsubscribe-Post"):
            out.append(Check("unsubscribe_ok", "ok"))
        else:
            out.append(Check("unsubscribe_missing", "warn"))
    if attachments:
        out.append(Check("attachments", "info", {"files": [a["name"] or a["type"] for a in attachments][:10]}))
    return out


ORDER = {"bad": 0, "warn": 1, "info": 2, "ok": 3}


def summary(checks: list[Check]) -> str:
    """Short form for the X-Mailfilter-Trap-Summary header: score and problem codes."""
    score = next((c.params["score"] for c in checks if c.code == "spam"), None)
    problems = [f"{c.code}:{c.status}" for c in sorted(checks, key=lambda c: ORDER[c.status]) if c.status in ("bad", "warn")]
    return f"score={'' if score is None else score}; issues={','.join(problems)}"


def parse_summary(value: str | None) -> dict | None:
    if not value:
        return None
    m = re.match(r"\s*score=(-?[\d.]*);\s*issues=([\w:,]*)", value)
    if not m:
        return None
    issues = []
    for item in filter(None, m.group(2).split(",")):
        code, _, status = item.partition(":")
        issues.append({"code": code, "status": status or "warn"})
    return {"score": float(m.group(1)) if m.group(1) else None, "issues": issues}


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in sorted(checks, key=lambda c: ORDER.get(c.status, 9))]
