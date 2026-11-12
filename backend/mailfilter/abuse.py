"""Automatic protection against hacked senders.

Every minute (systemd timer mailfilter-abuse.timer -> `mailfilter-admin abuse-scan`)
the mail trace of the last minutes is checked per sending server (relay IP)
and per SMTP account. A sender is paused when

* a large share of its mail is spam (a hacked website or mailbox), or
* it suddenly sends far more than usual (a script gone wild).

Paused means: Postfix still accepts its mail, but keeps it in the hold queue
(see mailfilter_config, OutboundHold). Nothing is lost and nothing leaves the
server. Admins get an e-mail and release the sender with one click, choosing
to deliver or delete what was held.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import queuectl
from .audit import audit
from .gateway import apply_changes
from .models import OutboundHold, TraceMessage, User
from .settings import get_settings
from .settings_store import get_setting, set_setting

log = logging.getLogger("mailfilter.abuse")

SETTINGS_KEY = "abuse"
NOT_SPAM_ACTIONS = ("no action", "greylist")
BASELINE_DAYS = 7


@dataclass
class AbuseSettings:
    enabled: bool = True
    window_minutes: int = 15
    # Spam: at least this many spam messages AND at least this share of all mail
    spam_min: int = 10
    spam_percent: int = 30
    # Volume: at least this many messages AND this many times the usual amount
    volume_min: int = 300
    volume_factor: int = 10
    # After a release, leave the sender alone for this long
    quiet_hours: int = 6
    # IP addresses and SMTP accounts that are never paused automatically
    exempt: list[str] = field(default_factory=list)
    # Extra addresses for the alert (admins always get it)
    notify: list[str] = field(default_factory=list)


def load_settings(db: Session) -> AbuseSettings:
    data = get_setting(db, SETTINGS_KEY) or {}
    known = AbuseSettings.__dataclass_fields__
    return AbuseSettings(**{k: v for k, v in data.items() if k in known})


def save_settings(db: Session, s: AbuseSettings) -> None:
    set_setting(db, SETTINGS_KEY, asdict(s))


# ---- who sent what ------------------------------------------------------------------
def _source_columns():
    account = func.lower(TraceMessage.sasl_user)
    ip = func.host(TraceMessage.client_ip)
    return account, ip


def _is_local(ip: str | None) -> bool:
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return a.is_loopback or a.is_unspecified


@dataclass
class Activity:
    kind: Literal["ip", "account"]
    value: str
    total: int = 0
    spam: int = 0
    usual: float = 0.0


def activity(db: Session, now: datetime, minutes: int) -> list[Activity]:
    """Outbound messages per sender in the last `minutes`, with the usual amount for such a period."""
    account, ip = _source_columns()
    start = now - timedelta(minutes=minutes)
    is_spam = func.coalesce(TraceMessage.spam_action, "no action").not_in(NOT_SPAM_ACTIONS)
    rows = db.execute(
        select(account, ip, func.count(), func.count().filter(is_spam))
        .where(TraceMessage.direction == "outbound", TraceMessage.first_seen >= start,
               TraceMessage.first_seen <= now, TraceMessage.is_bounce.is_(False))
        .group_by(account, ip)
    ).all()
    found: dict[tuple[str, str], Activity] = {}
    for acc, addr, total, spam in rows:
        if acc:
            key = ("account", acc)
        elif addr and not _is_local(addr):
            key = ("ip", addr)
        else:
            continue  # the Mailfilter's own mail (alerts, reports) is never paused
        a = found.setdefault(key, Activity(kind=key[0], value=key[1]))
        a.total += total
        a.spam += spam

    if found:
        periods = BASELINE_DAYS * 24 * 60 / minutes
        base_start = start - timedelta(days=BASELINE_DAYS)
        for acc, addr, total in db.execute(
            select(account, ip, func.count())
            .where(TraceMessage.direction == "outbound", TraceMessage.first_seen >= base_start,
                   TraceMessage.first_seen < start)
            .group_by(account, ip)
        ).all():
            key = ("account", acc) if acc else ("ip", addr)
            if key in found:
                found[key].usual += total / periods
    return list(found.values())


def verdict(a: Activity, s: AbuseSettings) -> tuple[str, dict[str, Any]] | None:
    detail = {"total": a.total, "spam": a.spam, "minutes": s.window_minutes, "usual": round(a.usual, 1)}
    if a.spam >= s.spam_min and a.total and a.spam * 100 >= s.spam_percent * a.total:
        return "spam", detail
    if a.total >= s.volume_min and a.total >= s.volume_factor * max(a.usual, 1):
        return "volume", detail
    return None


def active_hold(db: Session, kind: str, value: str) -> OutboundHold | None:
    return db.execute(select(OutboundHold).where(
        OutboundHold.kind == kind, OutboundHold.value == value, OutboundHold.released_at.is_(None))).scalar_one_or_none()


def in_quiet_period(db: Session, kind: str, value: str, now: datetime) -> bool:
    return db.execute(select(func.count()).select_from(OutboundHold).where(
        OutboundHold.kind == kind, OutboundHold.value == value, OutboundHold.quiet_until > now)).scalar_one() > 0


# ---- the job ---------------------------------------------------------------------------
def scan(db: Session, now: datetime | None = None) -> list[OutboundHold]:
    """Pause every sender that crosses a limit. Returns the new pauses (already applied)."""
    now = now or datetime.now(timezone.utc)
    s = load_settings(db)
    if not s.enabled:
        return []
    exempt = {e.strip().lower() for e in s.exempt if e.strip()}
    new: list[OutboundHold] = []
    for a in activity(db, now, s.window_minutes):
        if a.value in exempt or active_hold(db, a.kind, a.value) or in_quiet_period(db, a.kind, a.value, now):
            continue
        v = verdict(a, s)
        if v is None:
            continue
        reason, detail = v
        h = OutboundHold(kind=a.kind, value=a.value, reason=reason, detail=detail, automatic=True)
        db.add(h)
        new.append(h)
    if not new:
        return []
    try:
        apply_changes(db)
    except HTTPException as e:
        log.error("could not pause senders: %s", e.detail)
        db.rollback()
        return []
    for h in new:
        audit(db, None, None, "sender_paused", h.value, kind=h.kind, reason=h.reason, **h.detail)
    db.commit()
    notify(db, new)
    return new


def pause(db: Session, user: User, kind: str, value: str, request=None) -> OutboundHold:
    """Pause a sender by hand."""
    if active_hold(db, kind, value.strip().lower()):
        raise HTTPException(409, "already_paused")
    h = OutboundHold(kind=kind, value=value.strip().lower(), reason="manual", detail={}, automatic=False)
    db.add(h)
    apply_changes(db)
    audit(db, request, user, "sender_paused", h.value, kind=kind, reason="manual")
    db.commit()
    return h


def held_messages(db: Session, h: OutboundHold) -> list[str]:
    """Queue IDs of mail this sender has waiting in the hold queue (from the mail trace)."""
    account, ip = _source_columns()
    match = account == h.value if h.kind == "account" else ip == h.value
    return list(db.execute(select(TraceMessage.queue_id).where(
        TraceMessage.status == "held", TraceMessage.done.is_(False), match)).scalars())


def release(db: Session, h: OutboundHold, user: User, held_mail: Literal["deliver", "delete", "keep"],
            request=None) -> dict[str, int]:
    """Lift the pause, then deliver or delete what was held. Returns counts."""
    now = datetime.now(timezone.utc)
    s = load_settings(db)
    h.released_at, h.released_by = now, user.id
    h.quiet_until = now + timedelta(hours=s.quiet_hours)
    apply_changes(db)
    audit(db, request, user, "sender_released", h.value, kind=h.kind, held_mail=held_mail)
    db.commit()
    done = failed = 0
    if held_mail != "keep":
        for qid in held_messages(db, h):
            try:
                (queuectl.release if held_mail == "deliver" else queuectl.delete)(qid)
                done += 1
            except queuectl.QueueError:
                failed += 1  # already gone, or the helper is not reachable
    return {"done": done, "failed": failed}


# ---- alert ----------------------------------------------------------------------------
def notify(db: Session, holds: list[OutboundHold]) -> None:
    from .mailer import MailError, send

    s = load_settings(db)
    to = set(s.notify)
    if get_settings().admin_email:
        to.add(get_settings().admin_email)
    to |= set(db.execute(select(User.email).where(User.role == "admin", User.is_active.is_(True))).scalars())
    if not to:
        return
    host = get_settings().hostname
    lines_en, lines_nl = [], []
    for h in holds:
        d = h.detail
        if h.reason == "spam":
            lines_en.append(f"- {h.value}: {d['spam']} of {d['total']} messages in {d['minutes']} minutes were spam")
            lines_nl.append(f"- {h.value}: {d['spam']} van {d['total']} berichten in {d['minutes']} minuten waren spam")
        else:
            lines_en.append(f"- {h.value}: {d['total']} messages in {d['minutes']} minutes (usually about {d['usual']})")
            lines_nl.append(f"- {h.value}: {d['total']} berichten in {d['minutes']} minuten (normaal ongeveer {d['usual']})")
    text = (
        "Mailfilter paused these senders because they suddenly sent spam or far more mail than usual.\n"
        "Their mail is kept on hold: nothing was lost and nothing was sent.\n\n"
        + "\n".join(lines_en)
        + f"\n\nCheck the server or account, then release it at https://{host}/relay\n\n"
        "----------------------------------------------------------------------\n\n"
        "Mailfilter heeft deze afzenders gepauzeerd omdat ze ineens spam of veel meer mail dan normaal verstuurden.\n"
        "Hun mail wordt vastgehouden: er is niets verloren gegaan en niets verstuurd.\n\n"
        + "\n".join(lines_nl)
        + f"\n\nControleer de server of het account en geef hem daarna vrij op https://{host}/relay\n"
    )
    subject = f"Mailfilter: {len(holds)} sender(s) paused / afzender(s) gepauzeerd"
    try:
        send(db, sorted(to), subject, text)
    except MailError as e:
        log.error("alert mail not sent: %s", e)
