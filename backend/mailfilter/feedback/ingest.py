"""Handle spam / not-spam reports from end users.

Postfix delivers mail for ``spam@<hostname>`` and ``notspam@<hostname>`` to
``mailfilter-spam-report`` (master.cf service ``mfreport``). Rspamd does not
filter these two addresses (settings.conf ``mf_spam_reports``), so a
reported virus or spam mail is never refused on the way in.

A report is only learned when:

* the reporter's address is in a domain Mailfilter handles,
* the reported message was really delivered by Mailfilter to that domain
  (found in the mail trace), so nobody outside can feed us made-up mail, and
* the report itself is proven to come from that domain (DMARC, or a DKIM
  signature / SPF pass of that domain), unless an admin switched that off.

Everything else is still stored, with the reason, so it shows up in the web
interface where an admin or the customer can learn it by hand.

Exit codes follow sysexits: 0 = handled (also for reports we refuse: bouncing
them would only bother the reporter), 75 = try again later (database down).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..models import Domain, SpamFeedback, TraceMessage, TraceRecipient, User, user_domain
from ..settings import get_settings
from ..settings_store import get_setting
from .parse import Original, parse_report

EX_TEMPFAIL = 75
SETTING = "spam_reports"
DEFAULTS = {"enabled": True, "require_auth": True, "auto_block": False, "per_hour": 30}
HAM_ADDRESSES = ("notspam", "ham", "nospam", "not-spam")
KEEP_DAYS = 400  # reports themselves
KEEP_RAW_DAYS = 30  # the copy of the reported message
MAX_RAW = 5 * 1024 * 1024
DUPLICATE_DAYS = 30
# How far the date in a plain forward may be from the trace (time zones, clock skew).
INLINE_WINDOW = timedelta(hours=14)
INLINE_LOOKBACK = timedelta(days=30)

Checker = Callable[[bytes, str, str, str, str], str]
Learner = Callable[[str, bytes], bool]


def options(db: Session) -> dict:
    stored = get_setting(db, SETTING, {}) or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def addresses(hostname: str | None = None) -> dict[str, str]:
    host = hostname or get_settings().hostname
    return {"spam": f"spam@{host}", "ham": f"notspam@{host}"}


def _aligned(a: str, b: str) -> bool:
    a, b = a.lower().strip("."), b.lower().strip(".")
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def verdict_from_symbols(symbols: dict, reporter_domain: str, envelope_sender: str) -> str:
    """Which check proves the report comes from the reporter's domain ("" = none)."""
    if "DMARC_POLICY_ALLOW" in symbols:
        return "dmarc"
    for opt in (symbols.get("R_DKIM_ALLOW") or {}).get("options") or []:
        if _aligned(str(opt).split(":", 1)[0], reporter_domain):
            return "dkim"
    env_domain = envelope_sender.rsplit("@", 1)[-1] if "@" in envelope_sender else ""
    if "R_SPF_ALLOW" in symbols and _aligned(env_domain, reporter_domain):
        return "spf"
    return ""


def rspamd_check(raw: bytes, client: str, helo: str, sender: str, reporter: str) -> str:
    """Let Rspamd check SPF, DKIM and DMARC of the report mail itself.

    The recipient is set to the reporter, so the normal inbound settings apply
    (not the no-filter rule of the report addresses)."""
    s = get_settings()
    headers = {"Password": s.rspamd_password, "Rcpt": reporter or "postmaster"}
    if client:
        headers["Ip"] = client
    if helo:
        headers["Helo"] = helo
    if sender:
        headers["From"] = sender
    try:
        r = httpx.post(f"{s.rspamd_controller}/checkv2", content=raw, headers=headers, timeout=30)
        r.raise_for_status()
        symbols = r.json().get("symbols") or {}
    except (httpx.HTTPError, ValueError):
        return ""
    return verdict_from_symbols(symbols, reporter.rsplit("@", 1)[-1], sender)


def rspamd_learn(kind: str, raw: bytes) -> bool:
    from ..queuectl import learn

    return learn(kind, raw)


def customer_domains(db: Session, domain: str) -> list[str]:
    """The hosted domain plus the other domains of the same customer(s), so a
    report from jan@klant.nl also covers mail delivered to jan@klant.com."""
    dom = db.execute(select(Domain).where(func.lower(Domain.name) == domain.lower())).scalar_one_or_none()
    if dom is None:
        return []
    user_ids = select(user_domain.c.user_id).join(User, User.id == user_domain.c.user_id).where(
        user_domain.c.domain_id == dom.id, User.role == "customer")
    names = db.execute(select(Domain.name).join(user_domain, user_domain.c.domain_id == Domain.id)
                       .where(user_domain.c.user_id.in_(user_ids))).scalars().all()
    return sorted({dom.name.lower(), *(n.lower() for n in names)})


def _in_domains(column, domains: list[str]):
    return or_(*[func.lower(column).like("%@" + d.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
                 for d in domains])


@dataclass
class Match:
    message: TraceMessage
    recipient: TraceRecipient


def find_in_trace(db: Session, original: Original, reporter: str, domains: list[str], now: datetime) -> Match | None:
    """The delivery of the reported message to the reporter's domain."""
    if not domains:
        return None
    base = (select(TraceMessage, TraceRecipient)
            .join(TraceRecipient, TraceRecipient.queue_id == TraceMessage.queue_id)
            .where(TraceRecipient.status == "sent", _in_domains(TraceRecipient.address, domains)))
    # The reporter's own mailbox first, then the newest delivery.
    order = (func.lower(TraceRecipient.address) == reporter.lower()).desc()
    mid = original.message_id.strip().strip("<>").strip()
    if mid:
        row = db.execute(base.where(TraceMessage.message_id == mid).order_by(order, TraceMessage.first_seen.desc()).limit(1)).first()
        if row:
            return Match(*row)
        if original.source == "attachment":
            return None
    if original.source != "inline" or not original.sender:
        return None
    stmt = base.where(func.lower(TraceMessage.sender) == original.sender.lower())
    if original.date:
        stmt = stmt.where(TraceMessage.first_seen.between(original.date - INLINE_WINDOW, original.date + INLINE_WINDOW))
    else:
        stmt = stmt.where(TraceMessage.first_seen >= now - INLINE_LOOKBACK)
    rows = db.execute(stmt.order_by(order, TraceMessage.first_seen.desc()).limit(50)).all()
    if not rows:
        return None
    if original.date:
        rows = sorted(rows, key=lambda r: (r[1].address.lower() != reporter.lower(),
                                           abs((r[0].first_seen - original.date).total_seconds())))
    return Match(*rows[0])


def learn_report(db: Session, fb: SpamFeedback, raw: bytes, learner: Learner, auto_block: bool) -> None:
    ok = learner(fb.kind, raw)
    fb.learned = fb.kind if ok else None
    fb.status = "learned" if ok else "not_learned"
    if not ok:
        fb.detail = "rspamd_refused"
    if ok and fb.kind == "spam" and auto_block and fb.sender and fb.recipient:
        block_sender(db, fb)


def block_sender(db: Session, fb: SpamFeedback, by: str = "spam report") -> bool:
    """Put the reported sender on the block list of the mailbox that got it."""
    from ..lists import ListError, norm_recipient, norm_sender, recipient_domain
    from ..models import SenderListEntry

    try:
        rcpt, snd = norm_recipient(fb.recipient), norm_sender(fb.sender)
    except ListError:
        return False
    dom = db.execute(select(Domain).where(func.lower(Domain.name) == recipient_domain(rcpt))).scalar_one_or_none()
    if dom is None:
        return False
    entry = db.execute(select(SenderListEntry).where(SenderListEntry.recipient == rcpt, SenderListEntry.sender == snd)).scalar_one_or_none()
    if entry is None:
        entry = SenderListEntry(recipient=rcpt, sender=snd, domain_id=dom.id, comment="", created_by=by)
        db.add(entry)
    entry.list = "block"
    entry.comment = f"Reported as spam (report #{fb.id})" if fb.id else "Reported as spam"
    fb.blocked = True
    return True


def _kind(rcpt_user: str, hint: str | None) -> str:
    kind = "ham" if rcpt_user.lower().split("+", 1)[0] in HAM_ADDRESSES else "spam"
    # Microsoft's Report button sends every kind of report to one mailbox and
    # says "Not junk" in the subject.
    if kind == "spam" and hint == "ham":
        kind = "ham"
    return kind


def handle(db: Session, raw: bytes, *, rcpt_user: str = "spam", sender: str = "", client: str = "", helo: str = "",
           checker: Checker | None = None, learner: Learner | None = None,
           now: datetime | None = None) -> SpamFeedback:
    now = now or datetime.now(timezone.utc)
    checker = checker or rspamd_check
    learner = learner or rspamd_learn
    opts = options(db)
    try:
        rep = parse_report(raw)
    except Exception:  # a mail the email package cannot read at all
        rep = None
    reporter = ((rep.reporter if rep else "") or sender or "").lower()[:320]
    fb = SpamFeedback(kind=_kind(rcpt_user, rep.kind_hint if rep else None), status="no_message", detail="",
                      source="attachment", reporter=reporter, domain=reporter.rsplit("@", 1)[-1] if "@" in reporter else "",
                      verified="", message_id="", sender="", recipient="", subject="", action="", blocked=False, handled_by="")
    db.add(fb)
    db.flush()
    original = rep.original if rep else None
    if original:
        fb.source = original.source
        fb.message_id = original.message_id.strip().strip("<>")[:998]
        fb.sender = original.sender[:320]
        fb.subject = original.subject[:500]

    if not opts["enabled"]:
        fb.status = "disabled"
        return _finish(db, fb, now)
    domains = customer_domains(db, fb.domain) if fb.domain else []
    if not domains:
        fb.status = "not_customer"
        return _finish(db, fb, now)
    recent = db.execute(select(func.count(SpamFeedback.id)).where(
        SpamFeedback.reporter == reporter, SpamFeedback.at >= now - timedelta(hours=1), SpamFeedback.id != fb.id)).scalar_one()
    if recent >= int(opts["per_hour"] or 0) > 0:
        fb.status = "rate_limited"
        return _finish(db, fb, now)
    if original is None:
        fb.status = "no_message"
        return _finish(db, fb, now)
    if len(original.raw) <= MAX_RAW:
        fb.raw = original.raw

    match = find_in_trace(db, original, reporter, domains, now)
    if match:
        m, r = match.message, match.recipient
        fb.queue_id, fb.recipient = m.queue_id, r.address.lower()[:320]
        fb.sender = (m.sender or fb.sender or "").lower()[:320]
        fb.message_id = fb.message_id or (m.message_id or "")[:998]
        fb.score, fb.action = m.spam_score, (m.spam_action or "")[:20]
        dup = db.execute(select(SpamFeedback.id).where(
            SpamFeedback.queue_id == m.queue_id, SpamFeedback.kind == fb.kind, SpamFeedback.id != fb.id,
            SpamFeedback.status == "learned", SpamFeedback.at >= now - timedelta(days=DUPLICATE_DAYS)).limit(1)).first()
        if dup:
            fb.status, fb.detail = "duplicate", f"#{dup[0]}"
            return _finish(db, fb, now)

    fb.verified = checker(raw, client, helo, sender, reporter) or ""
    if match is None:
        fb.status = "not_found"
    elif opts["require_auth"] and not fb.verified:
        fb.status = "unverified"
    else:
        db.flush()  # an id for the block list comment
        learn_report(db, fb, original.raw, learner, bool(opts["auto_block"]))
    return _finish(db, fb, now)


def _finish(db: Session, fb: SpamFeedback, now: datetime) -> SpamFeedback:
    db.flush()
    if fb.blocked:
        from ..lists import write_maps

        try:
            write_maps(db)
        except OSError:
            fb.blocked = False
            fb.detail = (fb.detail + " block_list_write_failed").strip()
    cleanup(db, now)
    db.commit()
    return fb


def cleanup(db: Session, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    db.execute(delete(SpamFeedback).where(SpamFeedback.at < now - timedelta(days=KEEP_DAYS)))
    db.execute(update(SpamFeedback).where(and_(SpamFeedback.at < now - timedelta(days=KEEP_RAW_DAYS),
                                               SpamFeedback.raw.is_not(None))).values(raw=None))


def main(argv: list[str] | None = None) -> int:
    """Postfix pipe: ``mailfilter-spam-report --to=USER --sender=S --client=IP --helo=H`` with the mail on stdin."""
    p = argparse.ArgumentParser(prog="mailfilter-spam-report")
    p.add_argument("--to", default="spam")
    p.add_argument("--sender", default="")
    p.add_argument("--client", default="")
    p.add_argument("--helo", default="")
    args, _ = p.parse_known_args(sys.argv[1:] if argv is None else argv)
    raw = sys.stdin.buffer.read()
    from ..db import session

    try:
        with session() as db:
            from ..modules import enabled

            if not enabled(db, "spam_reports"):
                print("spam report: dropped, the spam reports module is off")
                return 0
            fb = handle(db, raw, rcpt_user=args.to, sender=args.sender, client=args.client.strip("[]"),
                        helo=args.helo)
            line = f"spam report: {fb.kind} from {fb.reporter or '-'}: {fb.status} {fb.queue_id or ''} {fb.detail}"
    except OperationalError as exc:
        print(f"Spam report not handled yet, database unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return EX_TEMPFAIL
    print(" ".join(line.split()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
