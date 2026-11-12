"""Mail archive: search, read, download and export archived mail; settings per domain.

Admins see every domain and set archiving, retention and legal hold per domain.
Customers see the archive of their own domains only. Every search, view,
download and export is written to the audit log.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import threading
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..archive import chain, store
from ..archive.verify import verify as run_verify
from ..archive.verify import verify_message
from ..archive.maintain import alert_problems
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..models import ArchiveDomain, ArchiveMessage, ArchiveSeal, Domain, User
from ..settings import get_settings
from ..settings_store import get_setting, set_setting
from .gateway import _commit

router = APIRouter(prefix="/archive", tags=["archive"])

VERIFY_KEY = "archive.verify"
EXPORT_MAX = 10_000
EXPORT_MAX_BYTES = 4 * 1024 * 1024 * 1024
PAGE_MAX = 200


# ---- schemas -------------------------------------------------------------------------
class DomainOut(BaseModel):
    name: str
    enabled: bool = False
    configured: bool = False  # has an archive row (was ever switched on)
    retention_years: int = 7
    legal_hold: bool = False
    legal_hold_since: datetime | None = None
    legal_hold_note: str = ""
    messages: int = 0
    bytes: int = 0
    oldest: datetime | None = None


class DomainIn(BaseModel):
    enabled: bool
    retention_years: int = Field(default=7, ge=1, le=10)
    legal_hold: bool = False
    legal_hold_note: str = Field(default="", max_length=300)


class StatusOut(BaseModel):
    can_manage: bool
    messages: int
    bytes: int
    today: int
    last_seal: dict | None
    verify: dict | None
    verify_running: bool
    key_id: str
    public_key: str
    domains: list[DomainOut]


class Item(BaseModel):
    id: int
    domain: str
    seq: int
    received_at: datetime
    direction: str
    sender: str
    from_addr: str
    recipients: list[str]
    subject: str
    attachments: list[dict]
    size: int
    sealed: bool
    retain_until: date


class SearchOut(BaseModel):
    items: list[Item]
    total: int
    page: int
    size: int


class Detail(Item):
    queue_id: str
    message_id: str
    header_to: list[str]
    envelope_to: list[str]
    sha256: str
    entry_hash: str
    prev_hash: str
    intact: bool
    problem: str | None
    seal: dict | None
    legal_hold: bool
    text: str


# ---- helpers -------------------------------------------------------------------------
def _scope(user: User) -> list[str] | None:
    return None if user.is_admin else [d.lower() for d in user.domain_names]


def _visible(user: User, domain: str) -> bool:
    s = _scope(user)
    return s is None or domain.lower() in s


def _filters(user: User, *, q: str = "", domain: str = "", direction: str = "", sender: str = "",
             recipient: str = "", date_from: date | None = None, date_to: date | None = None,
             attachments: bool = False) -> list:
    cond = []
    scope = _scope(user)
    if scope is not None:
        cond.append(ArchiveMessage.domain.in_(scope or ["-"]))
    if domain:
        cond.append(ArchiveMessage.domain == domain.lower().strip())
    if direction in ("in", "out"):
        cond.append(ArchiveMessage.direction == direction)
    if q.strip():
        cond.append(ArchiveMessage.search.op("@@")(func.websearch_to_tsquery("simple", q.strip()[:500])))
    if sender.strip():
        s = f"%{sender.strip().lower()[:320]}%"
        cond.append(or_(ArchiveMessage.sender.ilike(s), ArchiveMessage.from_addr.ilike(s)))
    if recipient.strip():
        r = f"%{recipient.strip().lower()[:320]}%"
        cond.append(or_(func.array_to_string(ArchiveMessage.envelope_to, " ").ilike(r),
                        func.array_to_string(ArchiveMessage.header_to, " ").ilike(r)))
    if date_from:
        cond.append(ArchiveMessage.received_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        cond.append(ArchiveMessage.received_at < datetime.combine(date_to + timedelta(days=1), time.min,
                                                                  tzinfo=timezone.utc))
    if attachments:
        cond.append(func.jsonb_array_length(ArchiveMessage.attachments) > 0)
    return cond


def _item(m: ArchiveMessage) -> Item:
    return Item(id=m.id, domain=m.domain, seq=m.seq, received_at=m.received_at, direction=m.direction, sender=m.sender,
                from_addr=m.from_addr, recipients=list(m.envelope_to or m.header_to), subject=m.subject,
                attachments=list(m.attachments or []), size=m.size, sealed=m.sealed, retain_until=m.retain_until)


def _message(db: Session, user: User, msg_id: int) -> ArchiveMessage:
    m = db.get(ArchiveMessage, msg_id)
    if m is None or not _visible(user, m.domain):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return m


def _verify_state(db: Session, user: User) -> dict | None:
    rep = get_setting(db, VERIFY_KEY)
    if not rep:
        return None
    scope = _scope(user)
    if scope is None:
        return rep
    problems = [p for p in rep.get("problems", []) if p.get("domain") in scope]
    return {"ok": not problems, "started": rep.get("started"), "finished": rep.get("finished"), "problems": problems,
            "messages": None, "files": None, "seals": None, "domains": None}


# ---- overview and settings -------------------------------------------------------------
@router.get("/status", response_model=StatusOut)
def status_(db: DB, user: CurrentUser) -> StatusOut:
    scope = _scope(user)
    stats = {d: (n, b, o) for d, n, b, o in db.execute(
        select(ArchiveMessage.domain, func.count(), func.coalesce(func.sum(ArchiveMessage.stored_size), 0),
               func.min(ArchiveMessage.received_at)).group_by(ArchiveMessage.domain)).all()}
    rows = {r.name: r for r in db.execute(select(ArchiveDomain)).scalars()}
    names = set(rows) | {n.lower() for n in db.execute(select(Domain.name)).scalars()}
    if scope is not None:
        names &= set(scope)
    domains = []
    for name in sorted(names):
        r = rows.get(name)
        n, b, o = stats.get(name, (0, 0, None))
        domains.append(DomainOut(name=name, configured=r is not None, enabled=bool(r and r.enabled),
                                 retention_years=r.retention_years if r else 7, legal_hold=bool(r and r.legal_hold),
                                 legal_hold_since=r.legal_hold_since if r else None,
                                 legal_hold_note=r.legal_hold_note if r else "", messages=n, bytes=b, oldest=o))
    midnight = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    today_q = select(func.count()).select_from(ArchiveMessage).where(ArchiveMessage.received_at >= midnight)
    seal_q = select(ArchiveSeal).order_by(ArchiveSeal.created_at.desc()).limit(1)
    if scope is not None:
        today_q = today_q.where(ArchiveMessage.domain.in_(scope or ["-"]))
        seal_q = seal_q.where(ArchiveSeal.domain.in_(scope or ["-"]))
    seal = db.execute(seal_q).scalar_one_or_none()
    days = db.execute(select(func.count(func.distinct(ArchiveSeal.day)))
                      .where(ArchiveSeal.domain.in_(scope or ["-"]) if scope is not None else True)).scalar_one()
    keys = chain.public_keys(db)
    pub = get_setting(db, chain.KEY_PUBLIC) or {}
    return StatusOut(
        can_manage=user.is_admin, messages=sum(d.messages for d in domains), bytes=sum(d.bytes for d in domains),
        today=db.execute(today_q).scalar_one(),
        last_seal={"day": seal.day.isoformat(), "at": seal.created_at.isoformat() if seal.created_at else None,
                   "days": days} if seal else None,
        verify=_verify_state(db, user), verify_running=_verify_lock.locked(),
        key_id=pub.get("id", ""), public_key=keys.get(pub.get("id", ""), ""), domains=domains)


@router.put("/domains/{name}", response_model=DomainOut)
def set_domain(name: str, body: DomainIn, request: Request, db: DB, user: AdminUser) -> DomainOut:
    name = name.strip().lower()
    known = {n.lower() for n in db.execute(select(Domain.name)).scalars()}
    row = db.get(ArchiveDomain, name)
    if row is None and name not in known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if row is None:
        row = ArchiveDomain(name=name, last_seq=0, head=chain.ZERO)
        db.add(row)
    before = {"enabled": row.enabled, "retention_years": row.retention_years, "legal_hold": row.legal_hold}
    row.enabled, row.retention_years = body.enabled, body.retention_years
    if body.legal_hold and not row.legal_hold:
        row.legal_hold_since = datetime.now(timezone.utc)
    if not body.legal_hold:
        row.legal_hold_since = None
    row.legal_hold, row.legal_hold_note = body.legal_hold, body.legal_hold_note.strip()
    chain.signing_key(db)  # make the key now, so it is in the next backup
    _commit(db, request, user, "archive.domain", name, domain=name, before=before,
            after={"enabled": row.enabled, "retention_years": row.retention_years, "legal_hold": row.legal_hold,
                   "legal_hold_note": row.legal_hold_note})
    return next(d for d in status_(db, user).domains if d.name == name)


# ---- search and reading -----------------------------------------------------------------
@router.get("/search", response_model=SearchOut)
def search(db: DB, user: CurrentUser, request: Request, q: str = "", domain: str = "",
           direction: Literal["", "in", "out"] = "", sender: str = "", recipient: str = "",
           date_from: date | None = None, date_to: date | None = None, attachments: bool = False,
           page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=PAGE_MAX)) -> SearchOut:
    args = dict(q=q, domain=domain, direction=direction, sender=sender, recipient=recipient, date_from=date_from,
                date_to=date_to, attachments=attachments)
    cond = _filters(user, **args)
    total = db.execute(select(func.count()).select_from(ArchiveMessage).where(and_(True, *cond))).scalar_one()
    rows = db.execute(select(ArchiveMessage).where(and_(True, *cond))
                      .order_by(ArchiveMessage.received_at.desc(), ArchiveMessage.id.desc())
                      .offset((page - 1) * size).limit(size)).scalars().all()
    if any(v for v in args.values()) and page == 1:
        audit(db, request, user, "archive.search", domain or "", domain=domain or None,
              **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in args.items() if v and k != "domain"},
              results=total)
        db.commit()
    return SearchOut(items=[_item(m) for m in rows], total=total, page=page, size=size)


@router.get("/messages/{msg_id}", response_model=Detail)
def detail(msg_id: int, request: Request, db: DB, user: CurrentUser) -> Detail:
    m = _message(db, user, msg_id)
    root = store.root()
    problem = verify_message(m, root)
    body = ""
    if problem is None:
        try:
            body = store.parse(store.read(root, m.path)).text[:100_000]
        except Exception:
            body = ""
    seal = db.execute(select(ArchiveSeal).where(ArchiveSeal.domain == m.domain, ArchiveSeal.first_seq <= m.seq,
                                                ArchiveSeal.last_seq >= m.seq)).scalar_one_or_none()
    dom = db.get(ArchiveDomain, m.domain)
    audit(db, request, user, "archive.view", f"{m.domain} #{m.seq}", domain=m.domain, subject=m.subject[:200])
    db.commit()
    return Detail(**_item(m).model_dump(), queue_id=m.queue_id, message_id=m.message_id, header_to=list(m.header_to),
                  envelope_to=list(m.envelope_to), sha256=m.sha256, entry_hash=m.entry_hash, prev_hash=m.prev_hash,
                  intact=problem is None, problem=problem,
                  seal={"day": seal.day.isoformat(), "seal_hash": seal.seal_hash, "key_id": seal.key_id} if seal else None,
                  legal_hold=bool(dom and dom.legal_hold), text=body)


def _filename(m: ArchiveMessage) -> str:
    return f"{m.domain}-{m.seq:08d}.eml"


@router.get("/messages/{msg_id}/eml")
def download(msg_id: int, request: Request, db: DB, user: CurrentUser) -> Response:
    m = _message(db, user, msg_id)
    try:
        raw = store.read(store.root(), m.path)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_missing") from None
    audit(db, request, user, "archive.download", f"{m.domain} #{m.seq}", domain=m.domain, subject=m.subject[:200])
    db.commit()
    return Response(raw, media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(m)}"',
                             "X-Archive-SHA256": m.sha256})


README = """Mailfilter archive export
=========================

messages/      the archived messages, exactly as they were received or sent (.eml)
manifest.csv   one line per message: domain, sequence number, date, sender, subject, SHA-256
manifest.json  the same, plus the hash chain, the day seals and the archive's public key(s)

Check a message:   sha256sum messages/<file>   must equal the sha256 in the manifest.
Check the chain:   entry_hash = SHA-256 of these lines joined with a newline:
                   mfarchive-v1, domain, seq, received_at, direction, queue_id, sender,
                   sha256, size, retain_until, prev_hash
                   and prev_hash is the entry_hash of the message with seq - 1.
Check a seal:      seal_hash = SHA-256 of the seal's "payload"; the Ed25519 "signature"
                   over seal_hash must verify with the public key of its key_id.
"""


@router.get("/export")
def export(request: Request, db: DB, user: CurrentUser, q: str = "", domain: str = "",
           direction: Literal["", "in", "out"] = "", sender: str = "", recipient: str = "",
           date_from: date | None = None, date_to: date | None = None, attachments: bool = False,
           ids: str = "") -> FileResponse:
    import zipfile

    cond = _filters(user, q=q, domain=domain, direction=direction, sender=sender, recipient=recipient,
                    date_from=date_from, date_to=date_to, attachments=attachments)
    if ids:
        try:
            wanted = [int(x) for x in ids.split(",") if x.strip()][:EXPORT_MAX]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_input") from None
        cond.append(ArchiveMessage.id.in_(wanted))
    total = db.execute(select(func.count()).select_from(ArchiveMessage).where(and_(True, *cond))).scalar_one()
    if total == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing_to_export")
    if total > EXPORT_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "export_too_large")
    size = db.execute(select(func.coalesce(func.sum(ArchiveMessage.size), 0)).where(and_(True, *cond))).scalar_one()
    if size > EXPORT_MAX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "export_too_large")
    root = store.root()
    fd, path = tempfile.mkstemp(prefix="mf-archive-export-", suffix=".zip")
    os.close(fd)
    entries, seal_keys = [], set()
    csv_buf = io.StringIO()
    w = csv.writer(csv_buf)
    w.writerow(["file", "domain", "seq", "received_at", "direction", "sender", "from", "recipients", "subject",
                "size", "sha256", "entry_hash", "intact"])
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for m in db.execute(select(ArchiveMessage).where(and_(True, *cond))
                                .order_by(ArchiveMessage.domain, ArchiveMessage.seq)).scalars().yield_per(500):
                name = f"messages/{_filename(m)}"
                try:
                    raw = store.read(root, m.path)
                    intact = store.sha256(raw) == m.sha256 and chain.entry_hash_of(m) == m.entry_hash
                    z.writestr(name, raw)
                except FileNotFoundError:
                    intact, name = False, ""
                w.writerow([name, m.domain, m.seq, m.received_at.isoformat(), m.direction, m.sender, m.from_addr,
                            " ".join(m.envelope_to or m.header_to), m.subject, m.size, m.sha256, m.entry_hash,
                            "yes" if intact else "NO"])
                entries.append({"file": name, "domain": m.domain, "seq": m.seq,
                                "received_at": m.received_at.isoformat(timespec="microseconds"),
                                "direction": m.direction, "queue_id": m.queue_id, "sender": m.sender,
                                "sha256": m.sha256, "size": m.size, "retain_until": m.retain_until.isoformat(),
                                "prev_hash": m.prev_hash, "entry_hash": m.entry_hash,
                                "envelope_to": list(m.envelope_to), "intact": intact})
                seal_keys.add(m.domain)
            seals = []
            doms = sorted(seal_keys)
            for s in db.execute(select(ArchiveSeal).where(ArchiveSeal.domain.in_(doms or ["-"]))
                                .order_by(ArchiveSeal.domain, ArchiveSeal.day)).scalars():
                seals.append({"domain": s.domain, "day": s.day.isoformat(),
                              "payload": chain.seal_payload(domain=s.domain, day=s.day, first_seq=s.first_seq,
                                                            last_seq=s.last_seq, count=s.count, bytes_=s.bytes,
                                                            chain_head=s.chain_head,
                                                            envelope_digest=s.envelope_digest, prev_seal=s.prev_seal),
                              "seal_hash": s.seal_hash, "signature": s.signature, "key_id": s.key_id})
            manifest = {"exported_at": datetime.now(timezone.utc).isoformat(), "exported_by": user.email,
                        "server": get_settings().hostname, "count": len(entries), "messages": entries,
                        "seals": seals, "public_keys": chain.public_keys(db)}
            z.writestr("manifest.csv", csv_buf.getvalue())
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
            z.writestr("README.txt", README)
    except Exception:
        os.unlink(path)
        raise
    audit(db, request, user, "archive.export", domain or "", domain=domain or None, count=len(entries),
          **{k: v for k, v in dict(q=q, direction=direction, sender=sender, recipient=recipient,
                                   date_from=date_from.isoformat() if date_from else "",
                                   date_to=date_to.isoformat() if date_to else "").items() if v})
    db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return FileResponse(path, media_type="application/zip", filename=f"mailfilter-archive-{stamp}.zip",
                        background=BackgroundTask(os.unlink, path))


# ---- integrity check ------------------------------------------------------------------
_verify_lock = threading.Lock()


class VerifyIn(BaseModel):
    files: bool = True


def _run_verify(files: bool) -> None:
    from ..db import session

    try:
        with session() as db:
            def progress(rep):
                set_setting(db, VERIFY_KEY, {**rep.as_dict(), "running": True})
                db.commit()

            rep = run_verify(db, files=files, progress=progress)
            set_setting(db, VERIFY_KEY, {**rep.as_dict(), "running": False, "files_checked": files})
            db.commit()
            if not rep.ok:
                alert_problems(db, rep.problems)
    finally:
        _verify_lock.release()


@router.post("/verify")
def verify(body: VerifyIn, request: Request, db: DB, user: AdminUser) -> dict:
    if not _verify_lock.acquire(blocking=False):
        return {"started": False, "running": True}
    try:
        audit(db, request, user, "archive.verify", "", files=body.files)
        db.commit()
        threading.Thread(target=_run_verify, args=(body.files,), daemon=True).start()
    except Exception:
        _verify_lock.release()
        raise
    return {"started": True, "running": True}
