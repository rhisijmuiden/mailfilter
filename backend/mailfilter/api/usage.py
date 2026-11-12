"""Usage per customer (basis for invoices), CSV export, and who pays for what:
billing customers, resellers and tariffs."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import monthly, usage
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..models import BillingCustomer, Domain, Reseller, User

router = APIRouter(tags=["usage"])

Money = float


class LineOut(BaseModel):
    code: str
    quantity: float
    price: Money
    amount: Money


class DomainOut(BaseModel):
    domain: str
    customer_id: int | None
    customer: str
    mailboxes: int
    mail_in: int
    spam_virus: int
    mail_out: int
    archive_bytes: int
    archive_messages: int


class CustomerOut(BaseModel):
    id: int | None
    name: str
    ref: str
    reseller_id: int | None
    reseller: str
    domains: list[DomainOut]
    mailboxes: int
    mail_in: int
    mail_out: int
    archive_bytes: int
    lines: list[LineOut]
    amount: Money | None
    trend: list[int]


class UsageOut(BaseModel):
    month: str
    current: bool
    stored: bool
    can_recount: bool
    prices: bool  # amounts are shown to this user
    currency: str
    archive: bool  # the archive feature is installed
    customers: list[CustomerOut]
    domains: list[DomainOut]
    unassigned: list[str]


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


def _archive_installed() -> bool:
    try:
        from .. import archive  # noqa: F401
    except ImportError:
        return False
    return hasattr(archive, "usage_by_domain")


def _domain_out(d: usage.DomainUsage) -> DomainOut:
    return DomainOut(**d.__dict__)


@router.get("/usage", response_model=UsageOut)
def get_usage(user: CurrentUser, db: DB, month: str | None = None) -> UsageOut:
    scope = usage.Scope.of(user)
    u = usage.build(db, _month(month), scope)
    return UsageOut(
        month=u.month.strftime("%Y-%m"), current=u.current, stored=u.stored, can_recount=u.can_recount,
        prices=scope.prices, currency=u.currency, archive=_archive_installed(),
        customers=[CustomerOut(
            id=c.id, name=c.name, ref=c.ref, reseller_id=c.reseller_id, reseller=c.reseller,
            domains=[_domain_out(d) for d in c.domains], mailboxes=c.total("mailboxes"), mail_in=c.total("mail_in"),
            mail_out=c.total("mail_out"), archive_bytes=c.total("archive_bytes"),
            lines=[LineOut(code=ln.code, quantity=float(ln.quantity), price=float(ln.price), amount=float(ln.amount)) for ln in c.lines],
            amount=float(c.amount) if scope.prices else None, trend=c.trend) for c in u.customers],
        domains=[_domain_out(d) for d in u.domains],
        unassigned=[d.domain for d in u.domains if d.customer_id is None] if scope.all else [],
    )


@router.get("/usage/export")
def export_usage(user: CurrentUser, db: DB, month: str | None = None,
                 kind: Literal["customers", "domains", "lines"] = "customers",
                 sep: Literal[";", ","] = ";", lang: Literal["en", "nl"] | None = None,
                 reseller: str | None = None) -> Response:
    """CSV for the accounting system. ``reseller``: "" or "direct" = customers without reseller, or a reseller id."""
    scope = usage.Scope.of(user)
    if kind == "lines" and not scope.prices:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_only")
    u = usage.build(db, _month(month), scope)
    if reseller not in (None, "") and scope.all:
        rid = None if reseller == "direct" else int(reseller) if reseller.isdigit() else -1
        u.customers = [c for c in u.customers if c.reseller_id == rid]
        ids = {c.id for c in u.customers}
        u.domains = [d for d in u.domains if d.customer_id in ids]
    body = usage.to_csv(u, kind, lang or user.language, sep, prices=scope.prices)
    name = f"usage-{u.month:%Y-%m}-{kind}.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


class MonthIn(BaseModel):
    month: str


@router.post("/usage/recalculate", response_model=UsageOut)
def recalculate(body: MonthIn, request: Request, admin: AdminUser, db: DB) -> UsageOut:
    """Freeze a finished month again with today's customers and prices (e.g. after assigning domains)."""
    m = _month(body.month)
    now = datetime.now(timezone.utc)
    if not usage.month_done(m, now):
        raise HTTPException(status.HTTP_409_CONFLICT, "month_running")
    usage.store_month(db, m, now)
    audit(db, request, admin, "usage_recalculated", m.strftime("%Y-%m"))
    db.commit()
    return get_usage(admin, db, body.month)


# --- billing customers, resellers, tariffs ---------------------------------------------

class Tariffs(BaseModel):
    currency: Literal["EUR", "USD", "GBP"] = "EUR"
    per_domain: Decimal = Field(Decimal(0), ge=0, le=100000)
    per_mailbox: Decimal = Field(Decimal(0), ge=0, le=100000)
    per_1000_in: Decimal = Field(Decimal(0), ge=0, le=100000)
    per_1000_out: Decimal = Field(Decimal(0), ge=0, le=100000)
    per_gb_archive: Decimal = Field(Decimal(0), ge=0, le=100000)
    minimum: Decimal = Field(Decimal(0), ge=0, le=1000000)


class OwnTariffs(BaseModel):
    """Prices of one customer that differ from the general tariffs; empty = general price."""
    per_domain: Decimal | None = Field(None, ge=0, le=100000)
    per_mailbox: Decimal | None = Field(None, ge=0, le=100000)
    per_1000_in: Decimal | None = Field(None, ge=0, le=100000)
    per_1000_out: Decimal | None = Field(None, ge=0, le=100000)
    per_gb_archive: Decimal | None = Field(None, ge=0, le=100000)
    minimum: Decimal | None = Field(None, ge=0, le=1000000)
    fixed: Decimal | None = Field(None, ge=0, le=1000000)  # one fixed price per month instead


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ref: str = Field("", max_length=100)
    reseller_id: int | None = None
    domains: list[str] = []
    tariffs: OwnTariffs = OwnTariffs()
    note: str = Field("", max_length=2000)


class BillingCustomerOut(BaseModel):
    id: int
    name: str
    ref: str
    reseller_id: int | None
    domains: list[str]
    tariffs: dict
    note: str
    logins: int


class ResellerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ref: str = Field("", max_length=100)
    discount_percent: Decimal = Field(Decimal(0), ge=0, le=100)
    note: str = Field("", max_length=2000)


class ResellerOut(BaseModel):
    id: int
    name: str
    ref: str
    discount_percent: float
    note: str
    customers: int
    logins: list[str]


class BillingOut(BaseModel):
    customers: list[BillingCustomerOut]
    resellers: list[ResellerOut]
    tariffs: Tariffs
    domains: list[str]  # all domain names
    unassigned: list[str]


def _tariffs_json(t: BaseModel) -> dict:
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in t.model_dump().items() if v is not None}


def _billing(db) -> BillingOut:
    domains = list(db.execute(select(Domain).order_by(Domain.name)).scalars())
    users = list(db.execute(select(User).where(User.is_active.is_(True))).scalars())
    custs = []
    for c in db.execute(select(BillingCustomer).order_by(func.lower(BillingCustomer.name))).scalars():
        names = [d.name for d in domains if d.billing_customer_id == c.id]
        low = {n.lower() for n in names}
        logins = sum(1 for u in users if u.role == "customer" and low & {d.lower() for d in u.domain_names})
        custs.append(BillingCustomerOut(id=c.id, name=c.name, ref=c.ref, reseller_id=c.reseller_id, domains=names,
                                        tariffs=c.tariffs or {}, note=c.note, logins=logins))
    resellers = []
    for r in db.execute(select(Reseller).order_by(func.lower(Reseller.name))).scalars():
        resellers.append(ResellerOut(
            id=r.id, name=r.name, ref=r.ref, discount_percent=float(r.discount_percent), note=r.note,
            customers=sum(1 for c in custs if c.reseller_id == r.id),
            logins=[u.email for u in users if u.role == "reseller" and u.reseller_id == r.id]))
    return BillingOut(customers=custs, resellers=resellers, tariffs=Tariffs(**usage.load_tariffs(db)),
                      domains=[d.name for d in domains], unassigned=[d.name for d in domains if d.billing_customer_id is None])


@router.get("/billing", response_model=BillingOut)
def get_billing(_: AdminUser, db: DB) -> BillingOut:
    return _billing(db)


@router.put("/billing/tariffs", response_model=BillingOut)
def put_tariffs(body: Tariffs, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    usage.save_tariffs(db, _tariffs_json(body))
    audit(db, request, admin, "billing_tariffs", "", **_tariffs_json(body))
    db.commit()
    return _billing(db)


def _unique(db, model, name: str, own_id: int | None) -> None:
    other = db.execute(select(model).where(func.lower(model.name) == name.lower())).scalar_one_or_none()
    if other is not None and other.id != own_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "name_exists")


def _save_customer(db, c: BillingCustomer, body: CustomerIn) -> None:
    name = body.name.strip()
    _unique(db, BillingCustomer, name, c.id)
    if body.reseller_id is not None and db.get(Reseller, body.reseller_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_reseller")
    c.name, c.ref, c.reseller_id, c.note = name, body.ref.strip(), body.reseller_id, body.note
    c.tariffs = _tariffs_json(body.tariffs)
    db.add(c)
    db.flush()
    wanted = {n.strip().lower() for n in body.domains if n.strip()}
    found = {d.name.lower(): d for d in db.execute(select(Domain).where(func.lower(Domain.name).in_(wanted))).scalars()}
    missing = wanted - set(found)
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown_domain:{','.join(sorted(missing))}")
    for d in db.execute(select(Domain).where(Domain.billing_customer_id == c.id)).scalars():
        if d.name.lower() not in wanted:
            d.billing_customer_id = None
    for d in found.values():  # a domain has one payer: it moves from its previous customer
        d.billing_customer_id = c.id


@router.post("/billing/customers", response_model=BillingOut, status_code=201)
def create_customer(body: CustomerIn, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    c = BillingCustomer()
    _save_customer(db, c, body)
    audit(db, request, admin, "billing_customer_created", c.name, domains=body.domains, reseller=body.reseller_id)
    db.commit()
    return _billing(db)


@router.put("/billing/customers/{cid}", response_model=BillingOut)
def update_customer(cid: int, body: CustomerIn, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    c = db.get(BillingCustomer, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    _save_customer(db, c, body)
    audit(db, request, admin, "billing_customer_updated", c.name, domains=body.domains, reseller=body.reseller_id)
    db.commit()
    return _billing(db)


@router.delete("/billing/customers/{cid}", response_model=BillingOut)
def delete_customer(cid: int, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    c = db.get(BillingCustomer, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    audit(db, request, admin, "billing_customer_deleted", c.name)
    db.delete(c)  # its domains become unassigned; frozen months keep the name
    db.commit()
    return _billing(db)


@router.post("/billing/resellers", response_model=BillingOut, status_code=201)
def create_reseller(body: ResellerIn, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    _unique(db, Reseller, body.name.strip(), None)
    r = Reseller(name=body.name.strip(), ref=body.ref.strip(), discount_percent=body.discount_percent, note=body.note)
    db.add(r)
    audit(db, request, admin, "billing_reseller_created", r.name, discount=float(body.discount_percent))
    db.commit()
    return _billing(db)


@router.put("/billing/resellers/{rid}", response_model=BillingOut)
def update_reseller(rid: int, body: ResellerIn, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    r = db.get(Reseller, rid)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    _unique(db, Reseller, body.name.strip(), r.id)
    r.name, r.ref, r.discount_percent, r.note = body.name.strip(), body.ref.strip(), body.discount_percent, body.note
    audit(db, request, admin, "billing_reseller_updated", r.name, discount=float(body.discount_percent))
    db.commit()
    return _billing(db)


@router.delete("/billing/resellers/{rid}", response_model=BillingOut)
def delete_reseller(rid: int, request: Request, admin: AdminUser, db: DB) -> BillingOut:
    r = db.get(Reseller, rid)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    audit(db, request, admin, "billing_reseller_deleted", r.name)
    db.delete(r)  # its customers become direct customers, its logins see nothing
    db.commit()
    return _billing(db)
