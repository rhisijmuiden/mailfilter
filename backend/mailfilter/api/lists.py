"""Allow and block lists. Customers manage the lists of their own mailboxes
and domains; admins also manage the list for every domain ("*")."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import DB, CurrentUser, visible_domains
from ..lists import EVERYONE, ListError, norm_recipient, norm_sender, recipient_domain, write_maps
from ..models import Domain, SenderListEntry, User

router = APIRouter(prefix="/lists", tags=["lists"])

ListName = Literal["allow", "block"]


class EntryIn(BaseModel):
    list: ListName
    recipient: str = Field(max_length=320)
    sender: str = Field(max_length=320)
    comment: str = Field("", max_length=500)


class EntryOut(BaseModel):
    id: int
    list: ListName
    recipient: str
    sender: str
    scope: Literal["everyone", "domain", "mailbox"]
    sender_kind: Literal["address", "domain"]
    comment: str
    created_by: str
    created_at: datetime


class ListOut(BaseModel):
    items: list[EntryOut]
    total: int


class OptionsOut(BaseModel):
    domains: list[str]
    everyone: bool


def entry_out(e: SenderListEntry) -> EntryOut:
    scope = "everyone" if e.recipient == EVERYONE else ("mailbox" if "@" in e.recipient else "domain")
    return EntryOut(id=e.id, list=e.list, recipient=e.recipient, sender=e.sender, scope=scope,
                    sender_kind="address" if "@" in e.sender else "domain", comment=e.comment,
                    created_by=e.created_by, created_at=e.created_at)


def _scoped(stmt, user: User):
    domains = visible_domains(user)
    if domains is None:
        return stmt
    return stmt.join(Domain, Domain.id == SenderListEntry.domain_id).where(func.lower(Domain.name).in_([d.lower() for d in domains]))


def _bad(code: str) -> HTTPException:
    return HTTPException(422, code)


def save_maps(db: Session) -> None:
    """Write the Rspamd maps, then commit; nothing is saved when writing fails."""
    db.flush()
    try:
        write_maps(db)
    except OSError as e:
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "lists_write_failed") from e
    db.commit()


def add_entry(db: Session, user: User | None, list_name: str, recipient: str, sender: str,
              comment: str = "", created_by: str = "") -> SenderListEntry:
    """Add (or move to the other list) one entry. ``user`` None means the
    caller already checked access (the quarantine report links)."""
    try:
        rcpt = norm_recipient(recipient)
        snd = norm_sender(sender)
    except ListError as e:
        raise _bad(e.code) from e
    dom_name = recipient_domain(rcpt)
    domain = None
    if dom_name is None:
        if user is not None and not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "everyone_admin_only")
    else:
        domain = db.execute(select(Domain).where(func.lower(Domain.name) == dom_name)).scalar_one_or_none()
        if domain is None:
            raise _bad("unknown_domain")
        allowed = visible_domains(user) if user is not None else None
        if allowed is not None and dom_name not in {d.lower() for d in allowed}:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_domain")
    entry = db.execute(select(SenderListEntry).where(SenderListEntry.recipient == rcpt, SenderListEntry.sender == snd)).scalar_one_or_none()
    if entry is None:
        entry = SenderListEntry(recipient=rcpt, sender=snd, domain_id=domain.id if domain else None)
        db.add(entry)
    entry.list = list_name
    entry.comment = comment.strip()
    entry.created_by = created_by
    return entry


@router.get("", response_model=ListOut)
def list_entries(user: CurrentUser, db: DB, list: ListName | None = None, q: str = Query("", max_length=320)) -> ListOut:
    stmt = _scoped(select(SenderListEntry), user)
    if list:
        stmt = stmt.where(SenderListEntry.list == list)
    if q.strip():
        pat = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(SenderListEntry.sender.like(pat), SenderListEntry.recipient.like(pat), func.lower(SenderListEntry.comment).like(pat)))
    rows = db.execute(stmt.order_by(SenderListEntry.created_at.desc(), SenderListEntry.id.desc()).limit(2000)).scalars().all()
    return ListOut(items=[entry_out(e) for e in rows], total=len(rows))


@router.get("/options", response_model=OptionsOut)
def options(user: CurrentUser, db: DB) -> OptionsOut:
    domains = visible_domains(user)
    if domains is None:
        domains = list(db.execute(select(Domain.name).order_by(Domain.name)).scalars())
    return OptionsOut(domains=sorted(d.lower() for d in domains), everyone=user.is_admin)


@router.post("", response_model=EntryOut, status_code=201)
def create(body: EntryIn, request: Request, user: CurrentUser, db: DB) -> EntryOut:
    e = add_entry(db, user, body.list, body.recipient, body.sender, body.comment, user.email)
    db.flush()
    audit(db, request, user, f"list_{body.list}_add", f"{e.recipient} <- {e.sender}")
    save_maps(db)
    db.refresh(e)
    return entry_out(e)


@router.delete("/{entry_id}", status_code=204)
def remove(entry_id: int, request: Request, user: CurrentUser, db: DB) -> None:
    e = db.execute(_scoped(select(SenderListEntry), user).where(SenderListEntry.id == entry_id)).scalar_one_or_none()
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    audit(db, request, user, f"list_{e.list}_remove", f"{e.recipient} <- {e.sender}")
    db.delete(e)
    save_maps(db)
