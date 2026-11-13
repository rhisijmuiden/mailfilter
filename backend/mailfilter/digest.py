"""Daily quarantine report: one e-mail per recipient listing the mail held for
them, with a link per message to release it, release it and always allow the
sender, or block the sender.

The links carry a signed token (HMAC with APP_SECRET) naming the message and
the recipient, valid for TOKEN_DAYS. Opening a link only shows the message;
the action needs a click on that page (a POST), so link scanners in mail
programs cannot release anything by fetching the URL.

``mailfilter-admin quarantine-report`` runs every hour from a systemd timer;
it sends once a day, at the hour set in the web interface.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import branding, mailer
from .models import DigestDomain, DigestSent, Domain, TraceMessage
from .queuectl import QueueError, held_ids, raw_message
from .settings import get_settings
from .settings_store import get_setting, set_setting

SETTING_KEY = "quarantine_report"
LAST_RUN_KEY = "quarantine_report.last_run"
TOKEN_DAYS = 14
MAX_ITEMS = 50
VIRUS_SYMBOLS = ("CLAM_VIRUS", "CLAM_VIRUS_FAIL")


@dataclass
class ReportConfig:
    enabled: bool = False
    hour: int = 8  # local time (Settings.timezone)
    language: str = "en"  # en | nl
    only_new: bool = True  # skip recipients without new held mail since their last report


def load_config(db: Session) -> ReportConfig:
    data = get_setting(db, SETTING_KEY) or {}
    known = ReportConfig.__dataclass_fields__
    return ReportConfig(**{k: v for k, v in data.items() if k in known})


def save_config(db: Session, cfg: ReportConfig) -> None:
    set_setting(db, SETTING_KEY, asdict(cfg))


# --- tokens -------------------------------------------------------------------

def _key() -> bytes:
    secret = get_settings().app_secret
    if not secret:
        raise RuntimeError("APP_SECRET is not set")
    return hashlib.sha256(b"mailfilter-quarantine-report\0" + secret.encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(queue_id: str, recipient: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + TOKEN_DAYS * 86400)
    payload = _b64(json.dumps({"q": queue_id, "r": recipient, "e": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()[:18])
    return f"{payload}.{sig}"


class TokenError(ValueError):
    pass


def read_token(token: str, now: float | None = None) -> tuple[str, str]:
    """Return (queue_id, recipient) or raise TokenError("bad_token"/"expired")."""
    try:
        payload, sig = token.split(".", 1)
        want = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(want, sig):
            raise TokenError("bad_token")
        data = json.loads(_unb64(payload))
        queue_id, recipient, exp = str(data["q"]), str(data["r"]), int(data["e"])
    except TokenError:
        raise
    except (ValueError, KeyError, TypeError) as e:
        raise TokenError("bad_token") from e
    if exp < (now or time.time()):
        raise TokenError("expired")
    return queue_id, recipient


def action_url(token: str) -> str:
    return f"https://{get_settings().hostname}/report/{token}"


# --- collecting -----------------------------------------------------------------

@dataclass
class Item:
    queue_id: str
    sender: str
    subject: str
    received: datetime
    score: float | None
    virus: bool
    new: bool = False
    token: str = ""


@dataclass
class Report:
    recipient: str
    items: list[Item] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return sum(1 for i in self.items if i.new)


def is_virus(m: TraceMessage) -> bool:
    return any(s in (m.symbols or {}) for s in VIRUS_SYMBOLS)


def subject_of(queue_id: str) -> str:
    try:
        raw = raw_message(queue_id)
    except QueueError:
        return ""
    head = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
    msg = BytesHeaderParser(policy=default_policy).parsebytes(head)
    try:
        return str(msg.get("Subject", "") or "")[:200]
    except (ValueError, LookupError):
        return ""


def enabled_domains(db: Session) -> set[str]:
    off = set(db.execute(select(DigestDomain.domain_id).where(DigestDomain.enabled.is_(False))).scalars())
    return {name.lower() for did, name in db.execute(select(Domain.id, Domain.name)) if did not in off}


def collect(db: Session, now: datetime | None = None, domains: set[str] | None = None) -> list[Report]:
    """Held inbound mail grouped per recipient of a domain with the report on
    (or of ``domains``, for the "send me an example" button)."""
    now = now or datetime.now(timezone.utc)
    held = held_ids()
    domains = enabled_domains(db) if domains is None else {d.lower() for d in domains}
    last = dict(db.execute(select(DigestSent.recipient, DigestSent.sent_at)).all())
    rows = db.execute(
        select(TraceMessage)
        .where(TraceMessage.status == "quarantined", TraceMessage.done.is_(False), TraceMessage.direction == "inbound")
        .order_by(TraceMessage.first_seen.desc())
    ).scalars().all()
    reports: dict[str, Report] = {}
    subjects: dict[str, str] = {}
    for m in rows:
        if m.queue_id not in held:
            continue
        for r in m.recipients:
            addr = r.address.lower()
            if addr.rsplit("@", 1)[-1] not in domains:
                continue
            rep = reports.setdefault(addr, Report(addr))
            if len(rep.items) >= MAX_ITEMS:
                continue
            if m.queue_id not in subjects:
                subjects[m.queue_id] = subject_of(m.queue_id)
            seen = last.get(addr)
            rep.items.append(Item(
                queue_id=m.queue_id, sender=m.sender or "", subject=subjects[m.queue_id], received=m.first_seen,
                score=m.spam_score, virus=is_virus(m), new=seen is None or m.first_seen > seen,
                token=make_token(m.queue_id, addr, now.timestamp()),
            ))
    return [reports[k] for k in sorted(reports)]


# --- the e-mail ----------------------------------------------------------------

TEXT = {
    "en": {
        "subject": "Quarantine report: {n} message(s) held for {rcpt}",
        "intro": "Mailfilter held these messages for {rcpt} because they look like spam or are unsafe. "
                 "They are not in your mailbox. If one of them is real mail, open it and release it.",
        "new": "new", "from": "From", "subject_h": "Subject", "received": "Received", "open": "Release or block",
        "virus": "Contains a virus: only your administrator can release it.",
        "no_subject": "(no subject)", "no_sender": "(no sender, e.g. a bounce)",
        "footer": "You get this report once a day while mail is held for you. Messages are deleted from quarantine after a while. "
                  "Links in this report work for {days} days.",
    },
    "nl": {
        "subject": "Quarantaineoverzicht: {n} bericht(en) tegengehouden voor {rcpt}",
        "intro": "Mailfilter heeft deze berichten voor {rcpt} tegengehouden, omdat ze op spam lijken of onveilig zijn. "
                 "Ze staan niet in je mailbox. Is er toch een echte mail bij? Open hem en geef hem vrij.",
        "new": "nieuw", "from": "Van", "subject_h": "Onderwerp", "received": "Ontvangen", "open": "Vrijgeven of blokkeren",
        "virus": "Bevat een virus: alleen je beheerder kan dit vrijgeven.",
        "no_subject": "(geen onderwerp)", "no_sender": "(geen afzender, bijvoorbeeld een bounce)",
        "footer": "Je krijgt dit overzicht één keer per dag zolang er mail voor je is tegengehouden. Berichten worden na een tijd "
                  "uit de quarantaine verwijderd. De links in dit overzicht werken {days} dagen.",
    },
}

# House style (rhis-huisstijl): nacht, koraal, wolk.
NACHT, KORAAL, WOLK, MUTED, LINE = "#14213D", "#FF6B57", "#F6F7FB", "#4F5B73", "#DCE1EB"


def render(rep: Report, lang: str = "en", tz: str | None = None, brand: dict | None = None) -> tuple[str, str, str]:
    """Return (subject, plain text, html) for one recipient. ``brand``: see branding.get."""
    brand = brand or branding.DEFAULTS
    head_bg, accent, name = brand["primary"], brand["accent"], brand["name"]
    t = TEXT.get(lang, TEXT["en"])
    zone = ZoneInfo(tz or get_settings().timezone)
    fmt = lambda d: d.astimezone(zone).strftime("%d-%m-%Y %H:%M")  # noqa: E731
    subject = t["subject"].format(n=len(rep.items), rcpt=rep.recipient)
    lines = [t["intro"].format(rcpt=rep.recipient), ""]
    for i in rep.items:
        lines += [
            f"{'* ' if i.new else '  '}{t['from']}: {i.sender or t['no_sender']}",
            f"  {t['subject_h']}: {i.subject or t['no_subject']}",
            f"  {t['received']}: {fmt(i.received)}",
            f"  {t['virus']}" if i.virus else f"  {t['open']}: {action_url(i.token)}",
            "",
        ]
    lines.append(t["footer"].format(days=TOKEN_DAYS))
    text = "\n".join(lines)

    e = html.escape
    rows = []
    for i in rep.items:
        badge = (f'<span style="display:inline-block;background:#FFE7E3;color:#B8392A;border-radius:999px;'
                 f'padding:1px 8px;font-size:11px;font-weight:600;margin-right:6px">{e(t["new"])}</span>') if i.new else ""
        action = (f'<span style="color:{MUTED};font-size:12px">{e(t["virus"])}</span>' if i.virus else
                  f'<a href="{e(action_url(i.token))}" style="display:inline-block;background:{accent};color:{branding.on_color(accent)};'
                  f'text-decoration:none;font-weight:600;font-size:13px;border-radius:6px;padding:7px 12px">{e(t["open"])}</a>')
        rows.append(
            f'<tr><td style="padding:14px 16px;border-top:1px solid {LINE}">'
            f'<div style="font-size:14px;font-weight:600;color:{NACHT}">{badge}{e(i.subject or t["no_subject"])}</div>'
            f'<div style="font-size:13px;color:{MUTED};margin-top:2px">{e(i.sender or t["no_sender"])} · {e(fmt(i.received))}</div>'
            f'<div style="margin-top:10px">{action}</div></td></tr>'
        )
    body = (
        f'<!doctype html><html><body style="margin:0;background:{WOLK};font-family:Archivo,Segoe UI,Arial,sans-serif;color:{NACHT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{WOLK}"><tr><td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#FFFFFF;border:1px solid {LINE};border-radius:12px;overflow:hidden">'
        f'<tr><td style="background:{head_bg};color:{branding.on_color(head_bg)};padding:18px 16px;font-size:18px;font-weight:700">'
        f'<span style="color:{accent}">●</span> {e(name)}</td></tr>'
        f'<tr><td style="padding:18px 16px 6px"><div style="font-size:20px;font-weight:700">{e(subject)}</div>'
        f'<p style="font-size:14px;line-height:1.5;color:{MUTED};margin:8px 0 0">{e(t["intro"].format(rcpt=rep.recipient))}</p></td></tr>'
        + "".join(rows) +
        f'<tr><td style="padding:14px 16px;border-top:1px solid {LINE};font-size:12px;color:{MUTED}">{e(t["footer"].format(days=TOKEN_DAYS))}</td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return subject, text, body


# --- sending -------------------------------------------------------------------

@dataclass
class RunResult:
    ran: bool
    reason: str = ""
    sent: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


def due(cfg: ReportConfig, last_run: str | None, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(get_settings().timezone))
    return cfg.enabled and local.hour >= cfg.hour and last_run != local.date().isoformat()


def run(db: Session, now: datetime | None = None, force: bool = False) -> RunResult:
    """Send the reports when they are due (or always with ``force``)."""
    now = now or datetime.now(timezone.utc)
    cfg = load_config(db)
    last_run = get_setting(db, LAST_RUN_KEY)
    if not force and not due(cfg, last_run, now):
        return RunResult(False, "disabled" if not cfg.enabled else "not_due")
    try:
        reports = collect(db, now)
    except QueueError:
        return RunResult(False, "queue_unavailable")
    res = RunResult(True)
    for rep in reports:
        if cfg.only_new and rep.new_count == 0:
            res.skipped += 1
            continue
        subject, text, body = render(rep, cfg.language, brand=branding.get(db))
        try:
            mailer.send(db, rep.recipient, subject, text, body)
        except mailer.MailError as e:
            res.failed.append(f"{rep.recipient}: {e.code}")
            continue
        row = db.get(DigestSent, rep.recipient)
        if row is None:
            db.add(DigestSent(recipient=rep.recipient, sent_at=now))
        else:
            row.sent_at = now
        res.sent += 1
    set_setting(db, LAST_RUN_KEY, now.astimezone(ZoneInfo(get_settings().timezone)).date().isoformat())
    db.commit()
    return res


def last_sent(db: Session) -> datetime | None:
    return db.execute(select(func.max(DigestSent.sent_at))).scalar_one_or_none()
