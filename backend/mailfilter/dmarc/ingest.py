"""Store DMARC reports, and the command Postfix pipes report mail into.

Postfix delivers mail for dmarc-reports@<hostname> to
``mailfilter-dmarc-ingest`` (see master.cf, service ``mfdmarc``). Exit codes
follow sysexits: 0 = done (also for mail that is not a report: bouncing it
would only bother the reporter), 75 = try again later (database down).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..models import DmarcIngest, DmarcReport, DmarcRow
from .parse import Report, ReportError, parse_file, parse_mail

KEEP_DAYS = 400  # a bit over a year, so a year can be compared
MAX_PTR = 300  # reverse lookups per mail; more IPs keep an empty host name
EX_TEMPFAIL = 75


def _resolver():
    from mailfilter_dns.resolver import DnsResolver

    return DnsResolver(timeout=2.0)


class HostNames:
    """Reverse DNS for report IPs, reusing names already stored."""

    def __init__(self, db: Session, resolver=None):
        self.db, self.resolver, self.cache, self.lookups = db, resolver, {}, 0

    def __call__(self, ip: str) -> str:
        if ip in self.cache:
            return self.cache[ip]
        host = self.db.execute(select(DmarcRow.source_host).where(DmarcRow.source_ip == ip, DmarcRow.source_host != "")
                               .order_by(DmarcRow.id.desc()).limit(1)).scalar_one_or_none() or ""
        if not host and self.lookups < MAX_PTR:
            self.lookups += 1
            try:
                self.resolver = self.resolver or _resolver()
                names = self.resolver.ptr(ip)
                host = names[0] if names else ""
            except Exception:  # DNS problems must never lose a report
                host = ""
        self.cache[ip] = host[:253]
        return self.cache[ip]


def store(db: Session, reports: list[Report], *, source: str = "mail", resolver=None) -> tuple[int, int]:
    """Save reports that are not stored yet. Returns (new, duplicates)."""
    names = HostNames(db, resolver)
    new = dup = 0
    for rep in reports:
        exists = db.execute(select(DmarcReport.id).where(
            DmarcReport.org_name == rep.org_name, DmarcReport.report_id == rep.report_id,
            DmarcReport.domain == rep.domain)).scalar_one_or_none()
        if exists:
            dup += 1
            continue
        row = DmarcReport(org_name=rep.org_name, org_email=rep.org_email, report_id=rep.report_id, domain=rep.domain,
                          begin=rep.begin, end=rep.end, p=rep.p, sp=rep.sp, pct=rep.pct, adkim=rep.adkim,
                          aspf=rep.aspf, messages=rep.messages, source=source)
        row.rows = [DmarcRow(source_ip=r.source_ip, source_host=names(r.source_ip), count=r.count,
                             disposition=r.disposition, dkim=r.dkim, spf=r.spf, header_from=r.header_from,
                             envelope_from=r.envelope_from, dkim_auth=r.dkim_auth, spf_auth=r.spf_auth,
                             reasons=r.reasons) for r in rep.rows]
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            new += 1
        except IntegrityError:  # the same report arriving twice at the same moment
            dup += 1
    return new, dup


def _status(new: int, dup: int, errors: list[str], found: int) -> tuple[str, str]:
    if new:
        return "ok", ", ".join(errors)
    if dup:
        return "duplicate", ", ".join(errors)
    if errors:
        return "error", ", ".join(errors)
    return "ignored", "no_report" if not found else ""


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def ingest_mail(db: Session, raw: bytes, *, sender: str = "", resolver=None) -> DmarcIngest:
    try:
        msg, reports, errors = parse_mail(raw)
    except Exception as exc:  # a mail the email package cannot read at all
        msg, reports, errors = None, [], [f"unreadable:{type(exc).__name__}"]
    new, dup = store(db, reports, source="mail", resolver=resolver)
    status, detail = _status(new, dup, errors, len(reports))
    from_addr = parseaddr(msg.get("From", ""))[1] if msg is not None else ""
    log = DmarcIngest(source="mail", sender=(sender or from_addr)[:320], subject=_decode(msg.get("Subject") if msg is not None else "")[:500],
                      status=status, detail=detail[:2000], domains=sorted({r.domain for r in reports}), reports=new)
    db.add(log)
    cleanup(db)
    db.commit()
    return log


def ingest_file(db: Session, name: str, data: bytes, *, actor: str = "", resolver=None) -> DmarcIngest:
    try:
        reports, errors = parse_file(name, data), []
    except ReportError as exc:
        reports, errors = [], [str(exc)]
    new, dup = store(db, reports, source="upload", resolver=resolver)
    status, detail = _status(new, dup, errors, len(reports))
    log = DmarcIngest(source="upload", sender=actor[:320], subject=name[:500], status=status, detail=detail[:2000],
                      domains=sorted({r.domain for r in reports}), reports=new)
    db.add(log)
    db.commit()
    return log


def cleanup(db: Session, now: datetime | None = None) -> None:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=KEEP_DAYS)
    db.execute(delete(DmarcReport).where(DmarcReport.end < cutoff))
    db.execute(delete(DmarcIngest).where(DmarcIngest.at < cutoff))


def main(argv: list[str] | None = None) -> int:
    """Postfix pipe: ``mailfilter-dmarc-ingest [sender]`` with the mail on stdin."""
    argv = sys.argv[1:] if argv is None else argv
    raw = sys.stdin.buffer.read()
    from ..db import session

    try:
        with session() as db:
            from ..modules import enabled

            if not enabled(db, "dmarc"):
                print("dmarc report: dropped, the DMARC reports module is off")
                return 0
            log = ingest_mail(db, raw, sender=argv[0] if argv else "")
    except OperationalError as exc:
        print(f"DMARC report not stored yet, database unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return EX_TEMPFAIL
    print(f"dmarc report: {log.status} {log.reports} new {' '.join(log.domains)} {log.detail}".strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
