"""Put one message in the archive, and the command Postfix pipes archive copies into.

Postfix adds <domain>@archive.mailfilter.invalid as an extra recipient to every
message of an archived domain (sender_bcc_maps / recipient_bcc_maps, written by
mailfilter_config). That copy is delivered to the mfarchive pipe:

    mailfilter-archive-deliver DOMAIN --queue-id=QUEUEID --sender=ADDR

The copy travels with the real message, so it is delivered only when the message
itself is: rejected spam never arrives, and a message held in the quarantine is
archived when it is released (and not at all when it is deleted).

Exit codes follow sysexits: 0 = stored (or not wanted), 75 = try again later
(Postfix keeps the copy queued, so nothing is lost when the database is down).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import ArchiveDomain, ArchiveMessage
from . import chain, store

EX_TEMPFAIL = 75
EX_USAGE = 64


def _in_domain(address: str, domain: str) -> bool:
    d = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    return d == domain or d.endswith("." + domain)


def archive_message(db: Session, domain: str, raw: bytes, *, queue_id: str = "", sender: str = "",
                    now: datetime | None = None, root: Path | None = None) -> ArchiveMessage | None:
    """Store `raw` for `domain`; returns None when the domain is not archived.
    Commits. Safe to call twice for the same queue ID (the second call is a no-op)."""
    domain = domain.strip().lower()
    root = root or store.root()
    now = now or datetime.now(timezone.utc)
    # The domain row is locked: one message at a time per domain keeps the chain in order.
    dom = db.execute(select(ArchiveDomain).where(ArchiveDomain.name == domain).with_for_update()).scalar_one_or_none()
    if dom is None or not dom.enabled:
        db.rollback()
        return None
    if queue_id:
        dup = db.execute(select(ArchiveMessage).where(ArchiveMessage.domain == domain,
                                                      ArchiveMessage.queue_id == queue_id)).scalars().first()
        if dup is not None:
            db.commit()  # nothing changed; releases the lock and keeps `dup` loaded
            return dup
    sender = (sender or "").strip().lower()[:320]
    meta = store.parse(raw)
    digest = store.sha256(raw)
    seq = dom.last_seq + 1
    until = chain.retain_until(now.date(), dom.retention_years, dom.retain_floor)
    direction = "out" if sender and _in_domain(sender, domain) else "in"
    rel = store.rel_path(domain, until, now, seq, digest)
    prev = dom.head
    h = chain.entry_hash(domain=domain, seq=seq, received_at=now, direction=direction, queue_id=queue_id,
                         sender=sender, sha256=digest, size=len(raw), retain_until=until, prev_hash=prev)
    if store.exists(root, rel):
        # Left behind by an attempt whose database commit failed: it holds the same message.
        if store.sha256(store.read(root, rel)) != digest:
            raise RuntimeError(f"{rel} exists with other content")
        stored = (root / rel).stat().st_size
    else:
        stored = store.write(root, rel, raw)
    m = ArchiveMessage(domain=domain, seq=seq, received_at=now, direction=direction, queue_id=queue_id,
                       sender=sender, from_addr=meta.from_addr, subject=meta.subject, message_id=meta.message_id,
                       header_to=meta.header_to, envelope_to=[], envelope_done=False, attachments=meta.attachments,
                       size=len(raw), stored_size=stored, sha256=digest, prev_hash=prev, entry_hash=h,
                       retain_until=until, path=rel, sealed=False)
    db.add(m)
    dom.last_seq, dom.head, dom.retain_floor = seq, h, until
    db.flush()
    db.execute(text("UPDATE archive_message SET search = to_tsvector('simple', :t) WHERE id = :id"),
               {"t": store.search_text(meta, sender, []), "id": m.id})
    db.commit()
    return m


def usage_by_domain(db: Session) -> dict[str, tuple[int, int]]:
    """{domain: (bytes on disk, number of messages)} for what is in the archive now."""
    rows = db.execute(select(ArchiveMessage.domain, func.coalesce(func.sum(ArchiveMessage.stored_size), 0),
                             func.count(ArchiveMessage.id)).group_by(ArchiveMessage.domain)).all()
    return {d.lower(): (int(b), int(n)) for d, b, n in rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mailfilter-archive-deliver")
    ap.add_argument("domain")
    ap.add_argument("--queue-id", default="")
    ap.add_argument("--sender", default="")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return EX_USAGE
    raw = sys.stdin.buffer.read(store.MAX_BYTES + 1)
    if len(raw) > store.MAX_BYTES:
        print("message too large for the archive", file=sys.stderr)
        return EX_TEMPFAIL  # keep it queued; an admin sees it in the queue
    from ..db import session

    try:
        with session() as db:
            archive_message(db, args.domain, raw, queue_id=args.queue_id[:40], sender=args.sender)
    except Exception as e:  # database or disk trouble: keep it queued and try again later
        print(f"archive: {e}", file=sys.stderr)
        return EX_TEMPFAIL
    return 0


if __name__ == "__main__":
    sys.exit(main())
