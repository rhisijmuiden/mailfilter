"""Monthly report per customer: the numbers in the web interface, the schedule,
who gets the e-mail, and "send me this report"."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import branding, mailer, monthly
from ..audit import audit
from ..deps import DB, CurrentUser
from ..models import MonthlyReportUser, MonthlyStats, User
from ..settings import get_settings
from ..settings_store import get_setting

router = APIRouter(prefix="/monthly-report", tags=["monthly-report"])


class StatsOut(BaseModel):
    received: int = 0
    delivered: int = 0
    spam: int = 0
    virus: int = 0
    refused: int = 0
    other: int = 0
    outbound: int = 0
    outbound_blocked: int = 0
    stopped: int = 0
    stopped_percent: float = 0


class Row(BaseModel):
    name: str
    stats: StatsOut


class CustomerRow(Row):
    id: int
    email: str


class Point(StatsOut):
    month: str


class ReportOut(BaseModel):
    month: str
    current: bool  # the month is still running
    stored: bool  # counted at the end of the month (otherwise from the trace)
    customer: int | None
    title: str
    total: StatsOut
    previous: StatsOut
    domains: list[Row]
    customers: list[CustomerRow]  # admins, all customers only
    history: list[Point]


class ConfigModel(BaseModel):
    enabled: bool = False
    day: int = Field(1, ge=1, le=28)
    hour: int = Field(8, ge=0, le=23)
    admin_summary: bool = True


class Recipient(BaseModel):
    id: int
    name: str
    email: str
    language: str
    domains: list[str]
    enabled: bool
    last_month: str | None
    sent_at: datetime | None


class SettingsOut(BaseModel):
    config: ConfigModel | None  # admins only
    recipients: list[Recipient]  # admins only
    me_enabled: bool
    last_run: str | None
    timezone: str


class Switch(BaseModel):
    id: int
    enabled: bool


class SettingsIn(BaseModel):
    config: ConfigModel | None = None
    users: list[Switch] = []
    me_enabled: bool | None = None


class ExampleIn(BaseModel):
    month: str | None = None
    customer: int | None = None


class ExampleOut(BaseModel):
    sent_to: str
    month: str


def _stats(s: monthly.Stats) -> StatsOut:
    return StatsOut(**s.to_dict())


def _month(text: str | None) -> date:
    now = datetime.now(timezone.utc)
    if not text:
        return monthly.add_months(monthly.month_of(now), -1)
    try:
        m = monthly.parse_month(text)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, "bad_month") from e
    if m > monthly.month_of(now):
        raise HTTPException(422, "bad_month")
    return m


def _target(db, user: User, customer: int | None) -> tuple[User | None, list[str], str]:
    """Which customer the report is about: (customer user or None for all, domains, title)."""
    if not user.is_admin:
        return user, user.domain_names, user.name or user.email
    if customer is None:
        return None, monthly.hosted_domains(db), ""
    target = db.get(User, customer)
    if target is None or target.role != "customer":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return target, target.domain_names, target.name or target.email


@router.get("", response_model=ReportOut)
def get_report(user: CurrentUser, db: DB, month: str | None = None, customer: int | None = None) -> ReportOut:
    m = _month(month)
    target, domains, title = _target(db, user, customer)
    rep = monthly.build_overview(db, m) if target is None else monthly.build(db, m, domains, title)
    rows = []
    if target is None:
        for c in monthly.customers(db):
            if c.domain_names:
                s = monthly.total({d: rep.domains.get(d.lower(), monthly.Stats()) for d in c.domain_names})
                rows.append(CustomerRow(id=c.id, name=c.name or c.email, email=c.email, stats=_stats(s)))
        rows.sort(key=lambda r: (-r.stats.received, r.name.lower()))
    stored = db.execute(select(MonthlyStats.id).where(MonthlyStats.month == m).limit(1)).first() is not None
    return ReportOut(
        month=m.strftime("%Y-%m"),
        current=m == monthly.month_of(datetime.now(timezone.utc)),
        stored=stored,
        customer=target.id if target is not None and user.is_admin else None,
        title=title,
        total=_stats(rep.total),
        previous=_stats(rep.previous),
        domains=[Row(name=d, stats=_stats(s)) for d, s in sorted(rep.domains.items(), key=lambda kv: (-kv[1].received, kv[0]))],
        customers=rows,
        history=[Point(month=pm.strftime("%Y-%m"), **s.to_dict()) for pm, s in monthly.history(db, domains, m)],
    )


def _settings(db, user: User) -> SettingsOut:
    pref = monthly.user_pref(db, user.id)
    out = SettingsOut(config=None, recipients=[], me_enabled=pref is None or pref.enabled,
                      last_run=get_setting(db, monthly.LAST_RUN_KEY), timezone=get_settings().timezone)
    if user.is_admin:
        out.config = ConfigModel(**monthly.load_config(db).__dict__)
        prefs = {p.user_id: p for p in db.execute(select(MonthlyReportUser)).scalars()}
        for c in monthly.customers(db):
            p = prefs.get(c.id)
            out.recipients.append(Recipient(
                id=c.id, name=c.name, email=c.email, language=c.language, domains=c.domain_names,
                enabled=p is None or p.enabled, last_month=p.last_month.strftime("%Y-%m") if p and p.last_month else None,
                sent_at=p.sent_at if p else None))
    return out


def _set_pref(db, user_id: int, enabled: bool) -> None:
    pref = db.get(MonthlyReportUser, user_id)
    if pref is None:
        db.add(MonthlyReportUser(user_id=user_id, enabled=enabled))
    else:
        pref.enabled = enabled


@router.get("/settings", response_model=SettingsOut)
def get_report_settings(user: CurrentUser, db: DB) -> SettingsOut:
    return _settings(db, user)


@router.put("/settings", response_model=SettingsOut)
def put_report_settings(body: SettingsIn, request: Request, user: CurrentUser, db: DB) -> SettingsOut:
    if (body.config is not None or body.users) and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_only")
    if body.config is not None:
        monthly.save_config(db, monthly.MonthlyConfig(**body.config.model_dump()))
    for sw in body.users:
        target = db.get(User, sw.id)
        if target is None or target.role != "customer":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        _set_pref(db, sw.id, sw.enabled)
    if body.me_enabled is not None:
        _set_pref(db, user.id, body.me_enabled)
    audit(db, request, user, "monthly_report_settings", "",
          config=body.config.model_dump() if body.config else None,
          users={s.id: s.enabled for s in body.users} or None, me_enabled=body.me_enabled)
    db.commit()
    return _settings(db, user)


@router.post("/example", response_model=ExampleOut)
def send_example(body: ExampleIn, user: CurrentUser, db: DB) -> ExampleOut:
    """Send the report shown on screen to the current user's own address."""
    m = _month(body.month)
    target, domains, title = _target(db, user, body.customer)
    rep = monthly.build_overview(db, m) if target is None else monthly.build(db, m, domains, title)
    subject, text, html = monthly.render(rep, user.language, user.name, brand=branding.get(db))
    try:
        mailer.send(db, user.email, subject, text, html)
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, e.code) from e
    return ExampleOut(sent_to=user.email, month=m.strftime("%Y-%m"))
