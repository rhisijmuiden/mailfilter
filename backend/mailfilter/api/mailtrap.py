"""Mailtrap: mailboxes that catch mail from test environments, and the caught mail.

Admins set up mailboxes (which IPs or login, who may look, how long mail is
kept). Admins, users of the mailbox's customer domain and users given access
read the mail, check it, forward it to themselves or delete it. Only admins
can still send a caught message to its real recipients ("send anyway").
"""

from __future__ import annotations

import re
import shutil
import smtplib
import socket
from email.header import decode_header, make_header
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

import mailfilter_config as mfc

from .. import mailer
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..gateway import MAILTRAP_KEY, mailtrap_enabled, problems_detail
from ..mailtrap import checks, store
from ..models import Domain, MailTrap, MailTrapNetwork, User
from ..settings import get_settings
from ..settings_store import set_setting
from .gateway import _commit, _scoped_domain, dovecot_hash, make_password

router = APIRouter(prefix="/mailtrap", tags=["mailtrap"])

PREVIEW_INLINE_LIMIT = 3 * 1024 * 1024  # inline images (cid:) up to this size are shown in the preview


# ---- schemas -------------------------------------------------------------------------
class NetworkIO(BaseModel):
    cidr: str = Field(min_length=2, max_length=50)
    description: str = Field(default="", max_length=200)


class BoxIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    domain: str | None = None  # customer domain: its users can open the mailbox
    networks: list[NetworkIO] = Field(default=[], max_length=50)
    smtp_login: bool = False
    imap: bool = False
    mode: Literal["all", "except"] = "all"
    allow: list[str] = Field(default=[], max_length=100)
    retention_days: int = Field(default=7, ge=1, le=365)
    max_messages: int = Field(default=1000, ge=1, le=100_000)
    users: list[int] = Field(default=[], max_length=200)  # extra users with access
    enabled: bool = True


class BoxOut(BoxIn):
    id: int
    slug: str
    login: str
    user_emails: list[str] = []
    total: int = 0
    unread: int = 0
    bytes: int = 0


class BoxCreated(BoxOut):
    password: str  # shown once


class StatusOut(BaseModel):
    enabled: bool
    can_manage: bool
    imap_host: str
    boxes: list[BoxOut]


class SettingsIn(BaseModel):
    enabled: bool


# ---- helpers -------------------------------------------------------------------------
def mailtrap_root() -> Path:
    return get_settings().mailtrap_dir


def _maildir(t: MailTrap) -> Path:
    return mailtrap_root() / t.login / "Maildir"


def _can_see(user: User, t: MailTrap) -> bool:
    if user.is_admin:
        return True
    if any(u.id == user.id for u in t.users):
        return True
    return t.domain is not None and t.domain.name in user.domain_names


def _box(db, user: User, box_id: int) -> MailTrap:
    t = db.get(MailTrap, box_id)
    if t is None or not _can_see(user, t):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return t


def _box_out(t: MailTrap, counts: bool = True) -> BoxOut:
    total = unread = size = 0
    if counts:
        for e in store.entries(_maildir(t)):
            total += 1
            unread += not e.seen
            size += e.size
    return BoxOut(id=t.id, slug=t.slug, login=t.login, name=t.name, domain=t.domain.name if t.domain else None,
                  networks=[NetworkIO(cidr=n.cidr, description=n.description) for n in t.networks],
                  smtp_login=t.smtp_login, imap=t.imap, mode=t.mode, allow=list(t.allow or []),
                  retention_days=t.retention_days, max_messages=t.max_messages, enabled=t.enabled,
                  users=[u.id for u in t.users], user_emails=[u.email for u in t.users],
                  total=total, unread=unread, bytes=size)


def _slug(db, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "box"
    slug, n = base, 2
    while db.execute(select(MailTrap.id).where(MailTrap.slug == slug)).first() or \
            db.execute(select(MailTrap.id).where(MailTrap.login == f"trap-{slug}")).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _set(db, t: MailTrap, body: BoxIn, user: User) -> None:
    dom = _scoped_domain(db, user, body.domain) if body.domain else None
    t.name = body.name.strip()
    t.domain_id = dom.id if dom else None
    t.smtp_login, t.imap, t.mode = body.smtp_login, body.imap, body.mode
    t.allow = [a.strip() for a in body.allow if a.strip()]
    t.retention_days, t.max_messages, t.enabled = body.retention_days, body.max_messages, body.enabled
    # Networks: normalise and check like the server does (same messages in EN and NL)
    probe = mfc.GatewayConfig(mailtraps=[mfc.MailTrap(slug="x", networks=[n.cidr for n in body.networks],
                                                      mode=body.mode, allow=t.allow)])
    problems = [p for p in mfc.validate(probe) if ".networks" in p.field or ".allow" in p.field]
    if problems:
        raise HTTPException(422, problems_detail(problems))
    t.allow = probe.mailtraps[0].allow
    cidrs = probe.mailtraps[0].networks
    taken = db.execute(select(MailTrapNetwork.cidr, MailTrap.name).join(MailTrap)
                       .where(MailTrapNetwork.cidr.in_(cidrs or [""]), MailTrapNetwork.mailtrap_id != (t.id or -1))).first()
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "network_in_other_mailbox", "cidr": taken[0],
                                                       "mailbox": taken[1]})
    desc = {c: n.description.strip() for c, n in zip(cidrs, body.networks)}
    have = {n.cidr: n for n in t.networks}
    t.networks = [have.get(c) or MailTrapNetwork(cidr=c) for c in dict.fromkeys(cidrs)]
    for n in t.networks:
        n.description = desc.get(n.cidr, n.description)
    t.users = list(db.execute(select(User).where(User.id.in_(body.users or [-1]))).scalars()) if body.users else []


def _decode(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except (ValueError, LookupError):
        return str(value)


# ---- mailboxes -----------------------------------------------------------------------
@router.get("", response_model=StatusOut)
def mailtrap_status(user: CurrentUser, db: DB) -> StatusOut:
    boxes = [t for t in db.execute(select(MailTrap).order_by(func.lower(MailTrap.name))).scalars() if _can_see(user, t)]
    return StatusOut(enabled=mailtrap_enabled(db), can_manage=user.is_admin, imap_host=get_settings().hostname,
                     boxes=[_box_out(t) for t in boxes])


@router.get("/unread")
def unread(user: CurrentUser, db: DB) -> dict:
    """For the menu: the badge count, and whether to show the Mailtrap at all
    (admins always; others when the module is on and they have a mailbox)."""
    n = boxes = 0
    for t in db.execute(select(MailTrap)).scalars():
        if _can_see(user, t):
            boxes += 1
            n += sum(1 for e in store.entries(_maildir(t)) if not e.seen)
    on = mailtrap_enabled(db)
    return {"unread": n if on or user.is_admin else 0, "enabled": on, "boxes": boxes,
            "visible": user.is_admin or (on and boxes > 0)}


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, admin: AdminUser, db: DB) -> dict:
    set_setting(db, MAILTRAP_KEY, body.enabled)
    _commit(db, request, admin, "mailtrap_switched", "on" if body.enabled else "off")
    return {"enabled": body.enabled}


@router.post("/boxes", response_model=BoxCreated, status_code=201)
def create_box(body: BoxIn, request: Request, admin: AdminUser, db: DB) -> BoxCreated:
    slug = _slug(db, body.name)
    password = make_password()
    t = MailTrap(slug=slug, login=f"trap-{slug}", password_hash=dovecot_hash(password))
    db.add(t)
    _set(db, t, body, admin)
    _commit(db, request, admin, "mailtrap_added", t.name, networks=[n.cidr for n in t.networks])
    db.refresh(t)
    return BoxCreated(**_box_out(t).model_dump(), password=password)


@router.put("/boxes/{box_id}", response_model=BoxOut)
def update_box(box_id: int, body: BoxIn, request: Request, admin: AdminUser, db: DB) -> BoxOut:
    t = _box(db, admin, box_id)
    _set(db, t, body, admin)
    _commit(db, request, admin, "mailtrap_updated", t.name, networks=[n.cidr for n in t.networks], enabled=t.enabled)
    db.refresh(t)
    return _box_out(t)


@router.post("/boxes/{box_id}/password", response_model=BoxCreated)
def new_password(box_id: int, request: Request, admin: AdminUser, db: DB) -> BoxCreated:
    t = _box(db, admin, box_id)
    password = make_password()
    t.password_hash = dovecot_hash(password)
    _commit(db, request, admin, "mailtrap_password_reset", t.login)
    db.refresh(t)
    return BoxCreated(**_box_out(t).model_dump(), password=password)


@router.delete("/boxes/{box_id}", status_code=204)
def delete_box(box_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    t = _box(db, admin, box_id)
    name, folder = t.name, _maildir(t).parent
    db.delete(t)
    _commit(db, request, admin, "mailtrap_deleted", name)
    shutil.rmtree(folder, ignore_errors=True)  # the caught mail goes with the mailbox


# ---- caught mail ---------------------------------------------------------------------
def _summary(e: store.Entry) -> dict:
    msg = store.parse_headers(e)
    s = checks.parse_summary(str(msg.get(store.H_SUMMARY, "")) or None)
    if s is None:  # e.g. a message added over IMAP: check it now
        full = store.parse(e)
        res = checks.run(full, e.size)
        s = checks.parse_summary(checks.summary(res))
    to = store.addresses(str(msg.get(store.H_TO, ""))) or store.addresses(str(msg.get("To", "")))
    return {
        "id": e.id, "seen": e.seen, "received": e.mtime, "size": e.size,
        "subject": _decode(msg.get("Subject")), "from": _decode(msg.get("From")),
        "to": to[:5], "to_count": len(to),
        "attachments": "multipart/mixed" in str(msg.get("Content-Type", "")).lower(),
        "score": s["score"] if s else None, "issues": s["issues"] if s else [],
    }


_cache: dict[tuple[str, float, int], dict] = {}


def _cached_summary(e: store.Entry) -> dict:
    key = (str(e.path.parent.parent / e.id), e.mtime, e.size)
    item = _cache.get(key)
    if item is None:
        if len(_cache) > 20_000:
            _cache.clear()
        item = _cache[key] = _summary(e)
    return {**item, "seen": e.seen}


@router.get("/boxes/{box_id}/messages")
def list_messages(box_id: int, user: CurrentUser, db: DB, q: str = "", limit: int = 200, offset: int = 0) -> dict:
    t = _box(db, user, box_id)
    items = store.entries(_maildir(t))
    out = []
    needle = q.strip().lower()
    for e in items:
        try:
            m = _cached_summary(e)
        except OSError:  # removed meanwhile (retention, IMAP)
            continue
        if needle and needle not in " ".join([m["subject"], m["from"], *m["to"]]).lower():
            continue
        out.append(m)
    limit = max(1, min(limit, 1000))
    return {"total": len(out), "messages": out[offset:offset + limit]}


def _entry(t: MailTrap, msg_id: str) -> store.Entry:
    e = store.find(_maildir(t), msg_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return e


def _inline_images(msg, html: str) -> str:
    """Show cid: images in the preview as data: URLs (remote images stay blocked)."""
    import base64

    total = 0
    for part in msg.walk():
        cid = str(part.get("Content-ID", "")).strip("<> ")
        if not cid or not part.get_content_type().startswith("image/"):
            continue
        data = part.get_payload(decode=True) or b""
        total += len(data)
        if total > PREVIEW_INLINE_LIMIT:
            break
        url = f"data:{part.get_content_type()};base64,{base64.b64encode(data).decode()}"
        html = html.replace(f"cid:{cid}", url)
    return html


@router.get("/boxes/{box_id}/messages/{msg_id}")
def get_message(box_id: int, msg_id: str, user: CurrentUser, db: DB) -> dict:
    t = _box(db, user, box_id)
    e = store.mark_seen(_entry(t, msg_id))
    msg = store.parse(e)
    html, text, attachments = checks.bodies(msg)
    raw = store.read_bytes(e)
    head = raw.split(b"\n\n", 1)[0].split(b"\r\n\r\n", 1)[0]
    return {
        "id": e.id, "received": e.mtime, "size": e.size, "seen": True,
        "subject": _decode(msg.get("Subject")), "from": _decode(msg.get("From")),
        "header_to": _decode(msg.get("To")), "cc": _decode(msg.get("Cc")),
        "envelope_from": (store.addresses(str(msg.get(store.H_FROM, ""))) or [""])[0],
        "envelope_to": store.addresses(str(msg.get(store.H_TO, ""))),
        "delivered_to": store.addresses(str(msg.get(store.H_DELIVERED, ""))),
        "client": str(msg.get(store.H_CLIENT, "")),
        "date": str(msg.get("Date", "")),
        "html": _inline_images(msg, html) if html else None,
        "text": text,
        "headers": head.decode("utf-8", "replace")[:100_000],
        "source": raw[:300_000].decode("utf-8", "replace"),
        "source_truncated": len(raw) > 300_000,
        "attachments": attachments,
        "checks": checks.as_dicts(checks.run(msg, e.size)),
    }


@router.get("/boxes/{box_id}/messages/{msg_id}/raw")
def download(box_id: int, msg_id: str, user: CurrentUser, db: DB) -> Response:
    t = _box(db, user, box_id)
    e = _entry(t, msg_id)
    return Response(store.read_bytes(e), media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{e.id[:60]}.eml"'})


@router.delete("/boxes/{box_id}/messages/{msg_id}", status_code=204)
def delete_message(box_id: int, msg_id: str, user: CurrentUser, db: DB) -> None:
    t = _box(db, user, box_id)
    store.delete(_entry(t, msg_id))


@router.delete("/boxes/{box_id}/messages", status_code=204)
def empty_box(box_id: int, request: Request, user: CurrentUser, db: DB) -> None:
    t = _box(db, user, box_id)
    for e in store.entries(_maildir(t)):
        store.delete(e)
    audit(db, request, user, "mailtrap_emptied", t.name)
    db.commit()


@router.post("/boxes/{box_id}/messages/{msg_id}/forward")
def forward(box_id: int, msg_id: str, request: Request, user: CurrentUser, db: DB) -> dict:
    """Send the caught mail to the logged-in user, as an attachment (it is never sent on as itself)."""
    t = _box(db, user, box_id)
    e = _entry(t, msg_id)
    msg = store.parse_headers(e)
    subject = _decode(msg.get("Subject")) or "(no subject)"
    nl = user.language == "nl"
    text = (f"Opgevangen in Mailtrap-postvak {t.name}. De mail zit als bijlage bij dit bericht.\n"
            if nl else f"Caught in Mailtrap mailbox {t.name}. The mail is attached to this message.\n")
    try:
        mailer.send(db, user.email, f"[Mailtrap] {subject}", text,
                    attach_message=(store.strip_our_headers(store.read_bytes(e)), "mailtrap.eml"))
    except mailer.MailError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"code": err.code, "detail": err.detail})
    audit(db, request, user, "mailtrap_forwarded", t.name, to=user.email, subject=subject[:200])
    db.commit()
    return {"sent_to": user.email}


def relay(raw: bytes, sender: str, recipients: list[str]) -> None:
    """Hand a message to the local Postfix for normal sending. Tests replace this."""
    with smtplib.SMTP("127.0.0.1", 25, local_hostname=get_settings().hostname or socket.getfqdn(), timeout=60) as s:
        refused = s.sendmail(sender or "", recipients, raw)
    if refused:
        raise smtplib.SMTPRecipientsRefused(refused)


@router.post("/boxes/{box_id}/messages/{msg_id}/release")
def release(box_id: int, msg_id: str, request: Request, admin: AdminUser, db: DB) -> dict:
    """Send a caught message to its real recipients after all (admins only, logged)."""
    t = _box(db, admin, box_id)
    e = _entry(t, msg_id)
    msg = store.parse_headers(e)
    done = set(store.addresses(str(msg.get(store.H_DELIVERED, ""))))
    recipients = [r for r in store.addresses(str(msg.get(store.H_TO, ""))) if r not in done]
    if not recipients:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_recipients")
    sender = (store.addresses(str(msg.get(store.H_FROM, ""))) or [""])[0]
    try:
        relay(store.strip_our_headers(store.read_bytes(e)), sender, recipients)
    except (OSError, smtplib.SMTPException) as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"code": "release_failed", "detail": str(err)[:500]})
    audit(db, request, admin, "mailtrap_released", t.name, recipients=recipients,
          subject=_decode(msg.get("Subject"))[:200])
    db.commit()
    return {"sent_to": recipients}


# ---- users that can be given access -------------------------------------------------
@router.get("/users")
def access_users(_: AdminUser, db: DB) -> list[dict]:
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role}
            for u in db.execute(select(User).where(User.is_active.is_(True)).order_by(User.email)).scalars()]


# Domains a mailbox can belong to (for the drawer's customer list)
@router.get("/domains")
def domains(_: AdminUser, db: DB) -> list[str]:
    return list(db.execute(select(Domain.name).order_by(Domain.name)).scalars())

