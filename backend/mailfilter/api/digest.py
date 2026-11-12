"""Daily quarantine report: settings, "send me an example", and the public
pages behind the links in the report (no login; the signed token is the key)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import digest, mailer
from ..audit import audit
from ..deps import DB, CurrentUser, visible_domains
from ..models import DigestDomain, Domain, QuarantineAction, TraceMessage
from ..queuectl import QueueError, delete, held_ids, learn, raw_message, release
from .lists import add_entry, save_maps

router = APIRouter(tags=["quarantine-report"])


class ConfigModel(BaseModel):
    enabled: bool = False
    hour: int = Field(8, ge=0, le=23)
    language: Literal["en", "nl"] = "en"
    only_new: bool = True


class DomainSwitch(BaseModel):
    name: str
    enabled: bool


class SettingsOut(BaseModel):
    config: ConfigModel | None  # admins only
    domains: list[DomainSwitch]
    last_sent: datetime | None
    timezone: str


class SettingsIn(BaseModel):
    config: ConfigModel | None = None
    domains: list[DomainSwitch] = []


class ExampleOut(BaseModel):
    sent_to: str
    messages: int


def _my_domains(db, user) -> list[Domain]:
    names = visible_domains(user)
    stmt = select(Domain).order_by(Domain.name)
    if names is not None:
        stmt = stmt.where(func.lower(Domain.name).in_([n.lower() for n in names]))
    return list(db.execute(stmt).scalars())


def _settings(db, user) -> SettingsOut:
    from ..settings import get_settings

    off = set(db.execute(select(DigestDomain.domain_id).where(DigestDomain.enabled.is_(False))).scalars())
    return SettingsOut(
        config=ConfigModel(**digest.load_config(db).__dict__) if user.is_admin else None,
        domains=[DomainSwitch(name=d.name, enabled=d.id not in off) for d in _my_domains(db, user)],
        last_sent=digest.last_sent(db),
        timezone=get_settings().timezone,
    )


@router.get("/quarantine-report/settings", response_model=SettingsOut)
def get_report_settings(user: CurrentUser, db: DB) -> SettingsOut:
    return _settings(db, user)


@router.put("/quarantine-report/settings", response_model=SettingsOut)
def put_report_settings(body: SettingsIn, request: Request, user: CurrentUser, db: DB) -> SettingsOut:
    if body.config is not None:
        if not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_only")
        digest.save_config(db, digest.ReportConfig(**body.config.model_dump()))
    mine = {d.name.lower(): d for d in _my_domains(db, user)}
    for sw in body.domains:
        d = mine.get(sw.name.lower())
        if d is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_domain")
        row = db.get(DigestDomain, d.id)
        if row is None:
            db.add(DigestDomain(domain_id=d.id, enabled=sw.enabled))
        else:
            row.enabled = sw.enabled
    audit(db, request, user, "quarantine_report_settings", "", config=body.config.model_dump() if body.config else None,
          domains={s.name: s.enabled for s in body.domains})
    db.commit()
    return _settings(db, user)


@router.post("/quarantine-report/example", response_model=ExampleOut)
def send_example(user: CurrentUser, db: DB) -> ExampleOut:
    """Send the current user one report with the mail held for their domains."""
    names = {d.name for d in _my_domains(db, user)}
    try:
        reports = digest.collect(db, domains=names)
    except QueueError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "queue_unavailable") from e
    combined = digest.Report(user.email)
    seen: set[str] = set()
    for rep in reports:
        for item in rep.items:
            if item.queue_id not in seen and len(combined.items) < digest.MAX_ITEMS:
                seen.add(item.queue_id)
                combined.items.append(item)
    if not combined.items:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing_held")
    combined.items.sort(key=lambda i: i.received, reverse=True)
    subject, text, html = digest.render(combined, user.language)
    try:
        mailer.send(db, user.email, subject, text, html)
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, e.code) from e
    return ExampleOut(sent_to=user.email, messages=len(combined.items))


# --- public pages behind the report links ----------------------------------------

class ReportItemOut(BaseModel):
    queue_id: str
    recipient: str
    sender: str
    subject: str
    received: datetime
    virus: bool
    state: Literal["held", "gone"]


class ReportActionIn(BaseModel):
    action: Literal["release", "release_allow", "block"]


class ReportActionOut(BaseModel):
    action: str
    listed: bool


def _from_token(db, token: str) -> tuple[TraceMessage, str]:
    try:
        queue_id, rcpt = digest.read_token(token)
    except digest.TokenError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND if str(e) == "bad_token" else status.HTTP_410_GONE, str(e)) from e
    m = db.get(TraceMessage, queue_id)
    if m is None or rcpt not in {r.address.lower() for r in m.recipients}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "bad_token")
    return m, rcpt


def _held(m: TraceMessage) -> bool:
    return m.status == "quarantined" and not m.done


@router.get("/report/{token}", response_model=ReportItemOut)
def report_item(token: str, db: DB) -> ReportItemOut:
    m, rcpt = _from_token(db, token)
    held = _held(m)
    if held:
        try:  # the trace catches up a moment after a release; Postfix knows right away
            held = m.queue_id in held_ids()
        except QueueError:
            pass
    return ReportItemOut(queue_id=m.queue_id, recipient=rcpt, sender=m.sender or "",
                         subject=digest.subject_of(m.queue_id) if held else "", received=m.first_seen,
                         virus=digest.is_virus(m), state="held" if held else "gone")


@router.post("/report/{token}", response_model=ReportActionOut)
def report_action(token: str, body: ReportActionIn, request: Request, db: DB) -> ReportActionOut:
    m, rcpt = _from_token(db, token)
    if not _held(m):
        raise HTTPException(status.HTTP_409_CONFLICT, "not_in_quarantine")
    releasing = body.action in ("release", "release_allow")
    if releasing and digest.is_virus(m):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "virus_admin_only")
    try:
        raw = raw_message(m.queue_id)
    except QueueError as e:
        code = "not_in_quarantine" if "not_in_quarantine" in str(e) else "queue_unavailable"
        raise HTTPException(status.HTTP_409_CONFLICT, code) from e
    listed = False
    if body.action != "release" and m.sender:
        add_entry(db, None, "allow" if releasing else "block", rcpt, m.sender, created_by=f"{rcpt} (quarantine report)")
        listed = True
    learned = "ham" if releasing else "spam"
    if not learn(learned, raw):
        learned = None
    try:
        (release if releasing else delete)(m.queue_id)
    except QueueError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "not_in_quarantine") from e
    db.add(QuarantineAction(queue_id=m.queue_id, action="released" if releasing else "deleted", learned=learned,
                            user_id=None, user_email=f"{rcpt} (report)"))
    audit(db, request, None, f"quarantine_report_{body.action}", m.queue_id, recipient=rcpt, sender=m.sender)
    if listed:
        save_maps(db)
    else:
        db.commit()
    return ReportActionOut(action=body.action, listed=listed)
