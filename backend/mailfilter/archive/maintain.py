"""Nightly archive work (mailfilter-admin archive-maintain, timer mailfilter-archive):

1. fill in the real (envelope) recipients from the mail log,
2. seal every finished day per domain (signed),
3. lock finished days on disk (root helper: chattr +i),
4. remove mail whose retention has passed, unless the domain has a legal hold.

Days are UTC days. Each step is safe to run again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..models import ArchiveDomain, ArchiveMessage, ArchivePurge, ArchiveSeal, TraceMessage, TraceRecipient
from . import chain, store

ENVELOPE_GRACE = timedelta(hours=2)  # the log needs a moment to show every recipient


@dataclass
class Result:
    envelopes: int = 0
    sealed: list[str] = field(default_factory=list)  # "domain day"
    purged: dict[str, int] = field(default_factory=dict)
    lock: str = ""
    errors: list[str] = field(default_factory=list)


def _visible_recipients(m: ArchiveMessage, rcpts: list[str]) -> list[str]:
    """Outgoing mail: every recipient. Incoming mail: only this domain's recipients,
    so one customer's archive never shows another customer's Bcc recipients."""
    rcpts = [r.lower() for r in rcpts if r and not store.is_archive_address(r)]
    if m.direction == "in":
        rcpts = [r for r in rcpts if r.rsplit("@", 1)[-1] == m.domain or r.rsplit("@", 1)[-1].endswith("." + m.domain)]
    return sorted(set(rcpts))


def fill_envelopes(db: Session, now: datetime, final_before: datetime | None = None) -> int:
    """Copy the envelope recipients from the mail trace. Messages received before
    `final_before` are marked done (whatever was found), the rest stay open."""
    rows = db.execute(select(ArchiveMessage).where(ArchiveMessage.envelope_done.is_(False),
                                                   ArchiveMessage.sealed.is_(False),
                                                   ArchiveMessage.received_at < now - ENVELOPE_GRACE)
                      .order_by(ArchiveMessage.id).limit(20_000)).scalars().all()
    n = 0
    for m in rows:
        rcpts: list[str] = []
        done_in_log = False
        if m.queue_id:
            rcpts = list(db.execute(select(TraceRecipient.address).where(TraceRecipient.queue_id == m.queue_id)).scalars())
            done_in_log = bool(db.execute(select(TraceMessage.done).where(TraceMessage.queue_id == m.queue_id)).scalar())
        found = _visible_recipients(m, rcpts)
        final = done_in_log or (final_before is not None and m.received_at < final_before)
        if not found and not final:
            continue
        if not found:
            # Not in the log (trace switched off or already cleaned up): the To/Cc headers are the best we have.
            found = _visible_recipients(m, m.header_to) if m.direction == "in" else sorted(m.header_to)
        m.envelope_to, m.envelope_done = found, final
        sender = m.sender
        db.flush()
        meta = store.Meta(subject=m.subject, from_addr=m.from_addr, header_to=m.header_to, attachments=m.attachments)
        # Recipients are added to the search index; the body words stay as they were.
        db.execute(text("UPDATE archive_message SET search = coalesce(search, ''::tsvector) || to_tsvector('simple', :t) "
                        "WHERE id = :id"), {"t": " ".join(found) + " " + store.search_text(meta, sender, found)[:5000],
                                            "id": m.id})
        n += 1
    db.commit()
    return n


def seal_days(db: Session, today: date) -> list[str]:
    """Seal every finished day (before `today`) that has unsealed mail, per domain, oldest first."""
    sealed = []
    start = datetime.combine(today, time.min, tzinfo=timezone.utc)
    domains = db.execute(select(ArchiveMessage.domain).where(ArchiveMessage.sealed.is_(False),
                                                             ArchiveMessage.received_at < start)
                         .group_by(ArchiveMessage.domain).order_by(ArchiveMessage.domain)).scalars().all()
    for domain in domains:
        while True:
            first = db.execute(select(ArchiveMessage).where(ArchiveMessage.domain == domain,
                                                            ArchiveMessage.sealed.is_(False),
                                                            ArchiveMessage.received_at < start)
                               .order_by(ArchiveMessage.seq).limit(1)).scalar_one_or_none()
            if first is None:
                break
            day = first.received_at.astimezone(timezone.utc).date()
            end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)
            msgs = db.execute(select(ArchiveMessage).where(ArchiveMessage.domain == domain,
                                                           ArchiveMessage.sealed.is_(False),
                                                           ArchiveMessage.received_at < end)
                              .order_by(ArchiveMessage.seq)).scalars().all()
            for m in msgs:
                if not m.envelope_done:
                    m.envelope_done = True
                    if not m.envelope_to:
                        m.envelope_to = (_visible_recipients(m, m.header_to) if m.direction == "in"
                                         else sorted(m.header_to))
            prev = db.execute(select(ArchiveSeal.seal_hash).where(ArchiveSeal.domain == domain)
                              .order_by(ArchiveSeal.day.desc()).limit(1)).scalar_one_or_none() or chain.ZERO
            existing = db.execute(select(ArchiveSeal).where(ArchiveSeal.domain == domain,
                                                            ArchiveSeal.day == day)).scalar_one_or_none()
            if existing is not None:
                # Mail with an earlier date arrived after the seal (clock change): seal it with the next day.
                day = max(day, existing.day) + timedelta(days=1)
                while db.execute(select(ArchiveSeal.id).where(ArchiveSeal.domain == domain,
                                                              ArchiveSeal.day == day)).first():
                    day += timedelta(days=1)
            s = ArchiveSeal(domain=domain, day=day, first_seq=msgs[0].seq, last_seq=msgs[-1].seq, count=len(msgs),
                            bytes=sum(m.stored_size for m in msgs), chain_head=msgs[-1].entry_hash,
                            envelope_digest=chain.envelope_digest([(m.seq, list(m.envelope_to)) for m in msgs]),
                            prev_seal=prev, seal_hash="", signature="", key_id="")
            s.seal_hash = chain.seal_hash_of(s)
            s.signature, s.key_id = chain.sign(db, s.seal_hash)
            db.flush()  # envelope changes first, while the rows are still unsealed
            db.add(s)
            db.execute(update(ArchiveMessage).where(ArchiveMessage.domain == domain,
                                                    ArchiveMessage.seq.between(s.first_seq, s.last_seq))
                       .values(sealed=True))
            db.commit()
            sealed.append(f"{domain} {day.isoformat()}")
    return sealed


def purge(db: Session, today: date, remove_month: Callable[[str, str], None]) -> dict[str, int]:
    """Remove mail whose retention date has passed (whole months, oldest first).
    remove_month(domain, "YYYY-MM") deletes the files (root helper); the rows go after it."""
    out: dict[str, int] = {}
    held = set(db.execute(select(ArchiveDomain.name).where(ArchiveDomain.legal_hold.is_(True))).scalars())
    rows = db.execute(select(ArchiveMessage.domain, ArchiveMessage.retain_until)
                      .where(ArchiveMessage.retain_until < today)
                      .group_by(ArchiveMessage.domain, ArchiveMessage.retain_until)
                      .order_by(ArchiveMessage.domain, ArchiveMessage.retain_until)).all()
    for domain, until in rows:
        if domain in held:
            continue
        # retain_until only grows along the chain, so this is the oldest part of it.
        last = db.execute(select(ArchiveMessage).where(ArchiveMessage.domain == domain,
                                                       ArchiveMessage.retain_until == until)
                          .order_by(ArchiveMessage.seq.desc()).limit(1)).scalar_one()
        stats = db.execute(select(func.count(), func.coalesce(func.sum(ArchiveMessage.stored_size), 0))
                           .where(ArchiveMessage.domain == domain, ArchiveMessage.retain_until == until)).one()
        remove_month(domain, f"{until:%Y-%m}")
        db.add(ArchivePurge(domain=domain, through_seq=last.seq, last_hash=last.entry_hash, count=int(stats[0]),
                            bytes=int(stats[1]), retain_until=until))
        db.execute(ArchiveMessage.__table__.delete().where(ArchiveMessage.domain == domain,
                                                          ArchiveMessage.retain_until == until))
        db.commit()
        out[domain] = out.get(domain, 0) + int(stats[0])
    return out


def run(db: Session, now: datetime | None = None, ctl: Callable[..., dict] | None = None) -> Result:
    from .. import audit as audit_mod

    now = now or datetime.now(timezone.utc)
    today = now.date()
    res = Result()
    if ctl is None:
        from .. import ctl as ctl_mod

        def ctl(*args: str) -> dict:  # noqa: F811
            return ctl_mod.call("archive", *args, timeout=3600)
    try:
        res.envelopes = fill_envelopes(db, now, final_before=datetime.combine(today, time.min, tzinfo=timezone.utc))
    except Exception as e:
        db.rollback()
        res.errors.append(f"envelopes: {e}")
    try:
        res.sealed = seal_days(db, today)
    except Exception as e:
        db.rollback()
        res.errors.append(f"seal: {e}")
    try:
        res.lock = str((ctl("lock") or {}).get("message", ""))
    except Exception as e:  # no root helper (development) or chattr not supported: the seals still protect
        res.errors.append(f"lock: {getattr(e, 'detail', e)}")
    try:
        res.purged = purge(db, today, lambda d, m: ctl("purge", d, m))
        for domain, n in res.purged.items():
            audit_mod.audit(db, None, None, "archive.purge", domain, domain=domain, count=n)
        db.commit()
    except Exception as e:
        db.rollback()
        res.errors.append(f"purge: {getattr(e, 'detail', e)}")
    return res


def alert_problems(db: Session, problems: list[dict]) -> None:
    """Send an alert (mail/Telegram/webhook, under the "backup" alert type) about integrity problems."""
    from ..notify import Alert, event

    codes: dict[str, int] = {}
    for p in problems:
        codes[p["code"]] = codes.get(p["code"], 0) + 1
    summary = ", ".join(f"{c} x{n}" for c, n in sorted(codes.items()))
    try:
        event(db, Alert("backup", "archive_integrity", "archive:integrity", "critical",
                        {"count": len(problems), "summary": summary}))
        db.commit()
    except Exception:  # an alert must never break the check itself
        db.rollback()


def alert_failed(db: Session, errors: list[str]) -> None:
    from ..notify import Alert, event

    try:
        event(db, Alert("backup", "archive_failed", "archive:maintain", "warning", {"message": "; ".join(errors)[:1000]}))
        db.commit()
    except Exception:
        db.rollback()
