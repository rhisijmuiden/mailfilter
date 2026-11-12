"""Read a spam report mail: who sent it, and which message it is about.

People report a message in three ways, and all three end up here:

* Outlook's built-in Report button (Microsoft 365): Microsoft sends the
  reported message as an attachment to the tenant's reporting mailbox, which
  forwards it to us.
* "Forward as attachment" (Outlook, Thunderbird, Gmail on the web): the
  original is a message/rfc822 part, or an ``.eml`` file.
* A plain forward (iPhone Mail, Android apps): the original's headers are
  gone, only a block like "From: / Subject: / Date:" sits in the text. We keep
  the sender and the date from that block to find the message in the mail
  trace, and learn from the forwarded text.
"""

from __future__ import annotations

import email
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

MAX_ORIGINAL = 10 * 1024 * 1024
MAX_PARTS = 200

# Words that say "this is good mail" at the start of a subject. Microsoft's
# Report button uses prefixes like "Not junk:"; people write "geen spam".
_HAM_SUBJECT = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?:not\s*junk|not\s*spam|no\s*spam|ham|geen\s*(?:ongewenste\s*e-?mail|spam)"
                          r"|niet\s*ongewenst|kein\s*spam|legit)\b", re.I)
_SPAM_SUBJECT = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?:junk|spam|phish(?:ing)?|ongewenst|ongewenste\s*e-?mail)\b", re.I)

# Header lines inside a plain forward, in the languages our customers use.
_FROM = r"(?:from|van|von|de|da)"
_SUBJ = r"(?:subject|onderwerp|betreff|objet|oggetto|asunto)"
_DATE = r"(?:date|sent|datum|verzonden|gesendet|envoy[ée]|enviado|inviato|data)"
_TO = r"(?:to|aan|an|[àa]|para|a)"
_LINE = re.compile(rf"^[ \t>*]*(?P<key>{_FROM}|{_SUBJ}|{_DATE}|{_TO})[ \t]*:[ \t]*\**[ \t]*(?P<val>.+?)\s*$", re.I | re.M)
_FWD_START = re.compile(r"(?:-{2,}\s*(?:forwarded message|original message|doorgestuurd bericht|oorspronkelijk bericht"
                        r"|weitergeleitete nachricht|urspr[üu]ngliche nachricht)\s*-*"
                        r"|begin (?:forwarded message|doorgestuurd bericht)\s*:"
                        r"|_{10,})", re.I)
_ADDR = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAILTO = re.compile(r"mailto:", re.I)


@dataclass
class Original:
    """The reported message as far as we could recover it."""

    source: str  # attachment | inline
    raw: bytes  # what Rspamd learns from
    message_id: str = ""
    sender: str = ""  # From address of the original
    subject: str = ""
    date: datetime | None = None
    recipients: list[str] = field(default_factory=list)


@dataclass
class ReportMail:
    reporter: str  # the From address of the report mail
    subject: str
    kind_hint: str | None  # spam | ham when the subject says so
    original: Original | None
    message: Message | None = None


def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return " ".join(str(make_header(decode_header(str(value)))).split())
    except Exception:
        return " ".join(str(value).split())


def _addr(value: str | None) -> str:
    return parseaddr(decode(value))[1].strip().lower()


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def kind_from_subject(subject: str) -> str | None:
    if _HAM_SUBJECT.search(subject):
        return "ham"
    if _SPAM_SUBJECT.search(subject):
        return "spam"
    return None


def _as_bytes(part: Message) -> bytes | None:
    """The bytes of an attached message, or None when the part is not one."""
    ctype = part.get_content_type()
    name = (part.get_filename() or "").lower()
    if ctype == "message/rfc822":
        payload = part.get_payload()
        if isinstance(payload, list) and payload:
            inner = payload[0]
            try:
                return inner.as_bytes()
            except Exception:
                return None
        data = part.get_payload(decode=True)
        return data or None
    if name.endswith(".eml") or ctype in ("text/rfc822-headers", "message/global"):
        data = part.get_payload(decode=True)
        return data or None
    return None


def _from_bytes(data: bytes) -> Original | None:
    data = data[:MAX_ORIGINAL]
    try:
        msg = email.message_from_bytes(data)
    except Exception:
        return None
    if not msg.keys():
        return None
    rcpts = [a.lower() for _, a in getaddresses([decode(v) for v in (msg.get_all("To", []) + msg.get_all("Cc", []))]) if a]
    return Original(source="attachment", raw=data, message_id=decode(msg.get("Message-ID")).strip()[:998],
                    sender=_addr(msg.get("From")), subject=decode(msg.get("Subject"))[:500],
                    date=_date(msg.get("Date")), recipients=rcpts)


def _text_body(msg: Message) -> str:
    """The first text part, as plain text (HTML is stripped roughly)."""
    plain = html = None
    for i, part in enumerate(msg.walk()):
        if i > MAX_PARTS:
            break
        if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AssertionError):
            text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain
    if html is not None:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        import html as html_mod

        return html_mod.unescape(re.sub(r"[ \t]+", " ", text))
    return ""


def parse_inline(body: str) -> Original | None:
    """Find the forwarded block in the text of a plain forward."""
    start = _FWD_START.search(body)
    text = body[start.end():] if start else body
    # The header block: the first lines with "From:", "Subject:", "Date:", "To:".
    values: dict[str, str] = {}
    end = 0
    for m in _LINE.finditer(text[:4000]):
        key = m.group("key").lower()
        group = ("from" if re.fullmatch(_FROM, key, re.I) else "subject" if re.fullmatch(_SUBJ, key, re.I)
                 else "date" if re.fullmatch(_DATE, key, re.I) else "to")
        if group == "from" and "from" in values:
            break  # a second forwarded block: stop at the first one
        values.setdefault(group, m.group("val"))
        end = m.end()
    sender_line = values.get("from", "")
    if not sender_line:
        return None
    sender_line = _MAILTO.sub("", sender_line)
    found = _ADDR.findall(sender_line)
    if not found:
        return None
    sender = found[-1].lower().strip(".")
    content = text[end:].strip()
    date = _loose_date(values.get("date", ""))
    rcpts = [a.lower() for a in _ADDR.findall(_MAILTO.sub("", values.get("to", "")))]
    subject = values.get("subject", "")[:500]
    # A small message Rspamd can learn from: the forwarded text with its header block.
    raw = EmailMessage()
    raw["From"] = sender
    raw["Subject"] = subject
    raw.set_content(content or subject)
    return Original(source="inline", raw=raw.as_bytes()[:MAX_ORIGINAL], sender=sender, subject=subject, date=date,
                    recipients=rcpts)


_MONTHS = {m: i + 1 for i, names in enumerate([
    ("jan", "january", "januari", "januar", "janvier"), ("feb", "february", "februari", "februar", "février", "fevrier"),
    ("mar", "march", "maart", "mrt", "märz", "maerz", "mars"), ("apr", "april", "avril"),
    ("may", "mei", "mai"), ("jun", "june", "juni", "juin"), ("jul", "july", "juli", "juillet"),
    ("aug", "august", "augustus", "août", "aout"), ("sep", "sept", "september", "septembre"),
    ("oct", "october", "okt", "oktober", "octobre"), ("nov", "november", "novembre"),
    ("dec", "december", "dez", "dezember", "décembre", "decembre")]) for m in names}


def _loose_date(value: str) -> datetime | None:
    """Dates as mail apps write them in a forward ("12 November 2026 at 10:15",
    "Wednesday, November 12, 2026 10:15 AM", "wo 12 nov. 2026 om 10:15")."""
    if not value:
        return None
    d = _date(value)
    if d:
        return d
    v = value.lower()
    year = re.search(r"\b(20\d\d)\b", v)
    month = next((_MONTHS[w] for w in re.findall(r"[a-zéäû]+", v) if w in _MONTHS), None)
    day = None
    if month is None:
        nums = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d)\b", v)
        if nums:
            day, month = int(nums.group(1)), int(nums.group(2))
            if month > 12:
                day, month = month, day
    else:
        days = [int(x) for x in re.findall(r"\b(\d{1,2})\b(?!:)", re.sub(r"\b20\d\d\b", "", v))]
        day = next((x for x in days if 1 <= x <= 31), None)
    if not (year and month and day):
        return None
    t = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?", v)
    hour, minute = (int(t.group(1)), int(t.group(2))) if t else (12, 0)
    if t and t.group(3) == "pm" and hour < 12:
        hour += 12
    if t and t.group(3) == "am" and hour == 12:
        hour = 0
    try:
        # Local time of the reporter is unknown; matching allows for that.
        return datetime(int(year.group(1)), month, day, min(hour, 23), minute, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_report(raw: bytes) -> ReportMail:
    msg = email.message_from_bytes(raw)
    subject = decode(msg.get("Subject"))[:500]
    reporter = _addr(msg.get("From"))
    original = None
    for i, part in enumerate(msg.walk()):
        if i > MAX_PARTS:
            break
        if part is msg:
            continue
        data = _as_bytes(part)
        if data:
            original = _from_bytes(data)
            if original:
                break
    if original is None:
        original = parse_inline(_text_body(msg))
    return ReportMail(reporter=reporter, subject=subject, kind_hint=kind_from_subject(subject), original=original, message=msg)
