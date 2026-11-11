"""Quarantine: messages held in Postfix's hold queue by Rspamd (spam score above
the quarantine threshold, viruses, risky attachments). Releasing teaches the
filter "this is good mail"; blocking teaches "this is spam"."""

from __future__ import annotations

import email
import email.policy
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from ..audit import audit
from ..deps import DB, CurrentUser
from ..models import QuarantineAction, TraceMessage
from ..queuectl import QueueError, delete, held_ids, learn, raw_message, release
from .trace import MessageOut, get_visible, message_out, scope, search

router = APIRouter(prefix="/quarantine", tags=["quarantine"])

VIRUS_SYMBOLS = ("CLAM_VIRUS", "CLAM_VIRUS_FAIL")
PREVIEW_HEADERS = ("From", "To", "Cc", "Reply-To", "Subject", "Date", "Message-ID", "Return-Path")
MAX_PREVIEW = 64 * 1024


class QuarantineItem(MessageOut):
    subject: str | None = None
    virus: bool = False
    reasons: list[str] = []


class QuarantineList(BaseModel):
    items: list[QuarantineItem]
    total: int
    queue_available: bool


class PreviewOut(BaseModel):
    headers: list[tuple[str, str]]
    text: str
    truncated: bool
    attachments: list[dict]


class DecisionIn(BaseModel):
    learn: bool = True


class DecisionOut(BaseModel):
    queue_id: str
    action: str
    learned: str | None
    at: datetime


def is_virus(m: TraceMessage) -> bool:
    return any(s in (m.symbols or {}) for s in VIRUS_SYMBOLS)


def reasons(m: TraceMessage) -> list[str]:
    sym = m.symbols or {}
    top = sorted(sym.items(), key=lambda kv: -(kv[1] if isinstance(kv[1], (int, float)) else 0))
    return [k for k, v in top if (v if isinstance(v, (int, float)) else 0) > 0][:6]


@router.get("", response_model=QuarantineList)
def list_quarantine(user: CurrentUser, db: DB, q: str = Query("", max_length=320), limit: int = Query(100, ge=1, le=500)) -> QuarantineList:
    stmt = select(TraceMessage).where(TraceMessage.status == "quarantined", TraceMessage.done.is_(False))
    stmt = scope(search(stmt, q), user).order_by(TraceMessage.first_seen.desc())
    rows = db.execute(stmt.limit(limit)).scalars().all()
    # Cross-check with Postfix: hide messages that are no longer in the hold queue.
    try:
        held = held_ids()
        rows = [m for m in rows if m.queue_id in held]
        available = True
    except QueueError:
        available = False
    items = [message_out(m, user, QuarantineItem, virus=is_virus(m), reasons=reasons(m)) for m in rows]
    return QuarantineList(items=items, total=len(items), queue_available=available)


def _held_message(db, user, queue_id: str) -> TraceMessage:
    m = get_visible(db, user, queue_id)
    if m.status != "quarantined" or m.done:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_in_quarantine")
    return m


def _raw(queue_id: str) -> bytes:
    try:
        return raw_message(queue_id)
    except QueueError as e:
        code = "not_in_quarantine" if "not_in_quarantine" in str(e) else "queue_unavailable"
        raise HTTPException(status.HTTP_409_CONFLICT if code == "not_in_quarantine" else status.HTTP_503_SERVICE_UNAVAILABLE, code) from e


def parse_preview(raw: bytes) -> PreviewOut:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    headers = [(h, str(msg[h])) for h in PREVIEW_HEADERS if msg[h] is not None]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            text = body.get_content()
        except (LookupError, ValueError):
            text = body.get_payload(decode=True).decode("utf-8", "replace") if body.get_payload(decode=True) else ""
        if body.get_content_subtype() == "html":
            import html as _html
            import re

            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"(?s)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
            text = _html.unescape(re.sub(r"(?s)<[^>]+>", "", text))
            text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    attachments = [
        {"filename": part.get_filename() or "", "content_type": part.get_content_type(),
         "size": len(part.get_payload(decode=True) or b"")}
        for part in msg.iter_attachments()
    ]
    return PreviewOut(headers=headers, text=text[:MAX_PREVIEW].strip(), truncated=len(text) > MAX_PREVIEW, attachments=attachments)


@router.get("/{queue_id}/preview", response_model=PreviewOut)
def preview(queue_id: str, user: CurrentUser, db: DB) -> PreviewOut:
    m = _held_message(db, user, queue_id)
    return parse_preview(_raw(m.queue_id))


def _decide(queue_id: str, body: DecisionIn, request: Request, user, db, action: str) -> DecisionOut:
    m = _held_message(db, user, queue_id)
    if action == "released" and is_virus(m) and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "virus_admin_only")
    learned = None
    if body.learn:
        kind = "ham" if action == "released" else "spam"
        if learn(kind, _raw(m.queue_id)):
            learned = kind
    try:
        (release if action == "released" else delete)(m.queue_id)
    except QueueError as e:
        code = "not_in_quarantine" if "not_in_quarantine" in str(e) else "queue_unavailable"
        raise HTTPException(status.HTTP_409_CONFLICT, code) from e
    a = QuarantineAction(queue_id=m.queue_id, action=action, learned=learned, user_id=user.id, user_email=user.email)
    db.add(a)
    audit(db, request, user, f"quarantine_{action}", m.queue_id, sender=m.sender, learned=learned)
    db.commit()
    return DecisionOut(queue_id=m.queue_id, action=action, learned=learned, at=a.at)


@router.post("/{queue_id}/release", response_model=DecisionOut)
def release_message(queue_id: str, body: DecisionIn, request: Request, user: CurrentUser, db: DB) -> DecisionOut:
    return _decide(queue_id, body, request, user, db, "released")


@router.post("/{queue_id}/delete", response_model=DecisionOut)
def delete_message(queue_id: str, body: DecisionIn, request: Request, user: CurrentUser, db: DB) -> DecisionOut:
    return _decide(queue_id, body, request, user, db, "deleted")
