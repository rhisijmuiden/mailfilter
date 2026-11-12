"""Usage per customer, as the basis for invoices.

What is counted per domain and month:
  * domains: every domain that was in Mailfilter at some point in the month
  * active mailboxes: unique addresses of the domain that received or sent real
    mail (delivered, so not addresses that only got spam or were refused)
  * mail in / out: the numbers of the monthly report (monthly_stats)
  * archive: the highest daily size of the domain's archive in the month, when
    the archive feature is installed (mailfilter.archive.usage_by_domain)

Who pays: a domain belongs to a billing customer, which may belong to a
reseller. Prices come from the general tariffs, optionally overruled per
customer, and a reseller gets its discount.

A finished month is frozen in usage_month / usage_customer_month (counts,
customer of each domain, prices), so it does not change when the trace is
pruned after 90 days, a domain moves to another customer or prices change.
An admin can freeze a month again ("recalculate") after fixing assignments.

``mailfilter-admin monthly-report`` (hourly timer) calls :func:`run`: it
measures today once and freezes finished months.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, distinct, func, select, union
from sqlalchemy.orm import Session

from . import monthly
from .models import (BillingCustomer, Domain, MonthlyStats, TraceMessage, TraceRecipient, UsageCustomerMonth,
                     UsageDaily, UsageMonth, User)
from .settings_store import get_setting, set_setting

log = logging.getLogger(__name__)

TARIFF_KEY = "billing.tariffs"
PRICE_FIELDS = ("per_domain", "per_mailbox", "per_1000_in", "per_1000_out", "per_gb_archive", "minimum")
CURRENCIES = ("EUR", "USD", "GBP")
GB = Decimal(1_000_000_000)
CENT = Decimal("0.01")
DELIVERED = ("delivered", "partly_delivered")


# --- prices ---------------------------------------------------------------------

def dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except ArithmeticError:
        return Decimal(0)


def default_tariffs() -> dict[str, Any]:
    return {"currency": "EUR", **{k: 0 for k in PRICE_FIELDS}}


def load_tariffs(db: Session) -> dict[str, Any]:
    data = get_setting(db, TARIFF_KEY) or {}
    out = default_tariffs()
    out.update({k: v for k, v in data.items() if k in out})
    return out


def save_tariffs(db: Session, data: dict[str, Any]) -> None:
    clean = default_tariffs()
    clean.update({k: v for k, v in data.items() if k in clean})
    set_setting(db, TARIFF_KEY, clean)


def effective(general: dict[str, Any], own: dict[str, Any] | None) -> dict[str, Any]:
    """General prices with the customer's own prices on top. ``fixed`` replaces everything."""
    out = dict(general)
    for k, v in (own or {}).items():
        if k in PRICE_FIELDS or k == "fixed":
            if v is not None and v != "":
                out[k] = v
    return out


# --- months and scope --------------------------------------------------------------

def month_done(m: date, now: datetime) -> bool:
    return m < monthly.month_of(now)


def trace_covers(m: date, now: datetime) -> bool:
    """The trace (90 days) still has the whole month."""
    return monthly.bounds(m)[0] >= now - timedelta(days=monthly.TRACE_DAYS)


@dataclass
class Scope:
    """What the logged-in user may see."""
    all: bool = False
    reseller_id: int | None = None
    domains: set[str] | None = None  # customer login: only these domains
    prices: bool = True

    @classmethod
    def of(cls, user: User) -> Scope:
        if user.is_admin:
            return cls(all=True)
        if user.role == "reseller":
            return cls(reseller_id=user.reseller_id or -1)
        return cls(domains={d.lower() for d in user.domain_names}, prices=False)


# --- measuring --------------------------------------------------------------------

def archive_usage(db: Session) -> dict[str, tuple[int, int]]:
    """{domain: (bytes, messages)} from the archive feature, or {} when it is not installed."""
    try:
        from .archive import usage_by_domain  # type: ignore[attr-defined]
    except ImportError:
        return {}
    try:
        return {str(k).lower(): (int(v[0]), int(v[1])) for k, v in (usage_by_domain(db) or {}).items()}
    except Exception:  # noqa: BLE001 - never let the archive stop the measurement
        log.exception("archive usage failed")
        return {}


def snapshot(db: Session, day: date | None = None) -> int:
    """Record today's domains (with their customer) and archive size. Safe to run every hour:
    the archive keeps the highest value of the day."""
    day = day or datetime.now(monthly.tz()).date()
    arch = archive_usage(db)
    have = {r.domain: r for r in db.execute(select(UsageDaily).where(UsageDaily.day == day)).scalars()}
    n = 0
    for d in db.execute(select(Domain)).scalars():
        name = d.name.lower()
        b, msgs = arch.get(name, (0, 0))
        row = have.get(name)
        if row is None:
            row = UsageDaily(day=day, domain=name, archive_bytes=0, archive_messages=0)
            db.add(row)
        row.customer_id = d.billing_customer_id
        row.archive_bytes = max(row.archive_bytes or 0, b)
        row.archive_messages = max(row.archive_messages or 0, msgs)
        n += 1
    db.flush()
    return n


def count_mailboxes(db: Session, m: date, domains: list[str]) -> dict[str, int]:
    """Unique addresses per domain that received or sent delivered mail in the month."""
    names = sorted({d.lower() for d in domains})
    if not names:
        return {}
    start, end = monthly.bounds(m)
    inbound = (select(func.lower(TraceRecipient.address).label("addr"))
               .join(TraceMessage, TraceMessage.queue_id == TraceRecipient.queue_id)
               .where(TraceMessage.first_seen >= start, TraceMessage.first_seen < end,
                      TraceMessage.direction.is_distinct_from("outbound"), TraceMessage.status.in_(DELIVERED),
                      TraceRecipient.status == "sent"))
    outbound = (select(func.lower(TraceMessage.sender).label("addr"))
                .where(TraceMessage.first_seen >= start, TraceMessage.first_seen < end,
                       TraceMessage.direction == "outbound", TraceMessage.status.in_(DELIVERED),
                       TraceMessage.is_bounce.is_not(True), TraceMessage.sender.is_not(None)))
    u = union(inbound, outbound).subquery()
    dom = func.split_part(u.c.addr, "@", 2)
    rows = db.execute(select(dom, func.count(distinct(u.c.addr))).where(dom.in_(names)).group_by(dom))
    out = {d: 0 for d in names}
    out.update({d: int(n) for d, n in rows})
    return out


def _month_domains(db: Session, m: date, now: datetime) -> dict[str, int | None]:
    """{domain: customer id} for every domain seen in the month (latest known customer)."""
    start, end = m, monthly.add_months(m, 1)
    out: dict[str, int | None] = {}
    for name, cid in db.execute(select(UsageDaily.domain, UsageDaily.customer_id)
                                .where(UsageDaily.day >= start, UsageDaily.day < end).order_by(UsageDaily.day)):
        out[name] = cid
    hosted = {d.name.lower(): d.billing_customer_id for d in db.execute(select(Domain)).scalars()}
    if m == monthly.month_of(now) or not out:
        # The running month, or no measurements yet (feature installed later): today's domains.
        for name, cid in hosted.items():
            out.setdefault(name, cid)
    for name in out:  # a domain that still exists has its current customer
        if name in hosted and m == monthly.month_of(now):
            out[name] = hosted[name]
    return out


def _archive_peak(db: Session, m: date) -> dict[str, tuple[int, int]]:
    start, end = m, monthly.add_months(m, 1)
    return {d: (int(b), int(n)) for d, b, n in db.execute(
        select(UsageDaily.domain, func.max(UsageDaily.archive_bytes), func.max(UsageDaily.archive_messages))
        .where(UsageDaily.day >= start, UsageDaily.day < end).group_by(UsageDaily.domain))}


# --- freezing a month ---------------------------------------------------------------

def store_month(db: Session, m: date, now: datetime | None = None) -> int:
    """Freeze a finished month (again). Mailboxes are counted again only while the
    trace still covers the month; otherwise the frozen counts stay."""
    now = now or datetime.now(timezone.utc)
    old = {r.domain: r for r in db.execute(select(UsageMonth).where(UsageMonth.month == m)).scalars()}
    recount = trace_covers(m, now) or not old
    domains = _month_domains(db, m, now)
    if not recount:
        for name, r in old.items():
            domains.setdefault(name, r.customer_id)
    if not db.execute(select(MonthlyStats.id).where(MonthlyStats.month == m).limit(1)).first() and trace_covers(m, now):
        monthly.store_month(db, m)
    boxes = count_mailboxes(db, m, list(domains)) if recount else {n: r.mailboxes for n, r in old.items()}
    arch = _archive_peak(db, m)
    customers = {c.id: c for c in db.execute(select(BillingCustomer)).scalars()}
    general = load_tariffs(db)

    db.execute(delete(UsageMonth).where(UsageMonth.month == m))
    db.execute(delete(UsageCustomerMonth).where(UsageCustomerMonth.month == m))
    db.flush()
    used: set[int] = set()
    for name, cid in sorted(domains.items()):
        c = customers.get(cid) if cid is not None else None
        b, n = arch.get(name, (0, 0))
        if not recount and name in old:
            b, n = max(b, old[name].archive_bytes), max(n, old[name].archive_messages)
        db.add(UsageMonth(month=m, domain=name, mailboxes=boxes.get(name, 0), archive_bytes=b, archive_messages=n,
                          customer_id=c.id if c else None, customer=c.name if c else ""))
        if c is not None:
            used.add(c.id)
    for cid in used:
        c = customers[cid]
        r = c.reseller
        db.add(UsageCustomerMonth(
            month=m, customer_id=c.id, customer=c.name, ref=c.ref, reseller_id=r.id if r else None,
            reseller=r.name if r else "", reseller_ref=r.ref if r else "",
            discount_percent=r.discount_percent if r else 0, tariffs=effective(general, c.tariffs)))
    db.flush()
    return len(domains)


def store_finished(db: Session, now: datetime | None = None) -> list[date]:
    """Freeze every finished month that the trace still fully covers and that is not frozen yet."""
    now = now or datetime.now(timezone.utc)
    current = monthly.month_of(now)
    stored = set(db.execute(select(UsageMonth.month).distinct()).scalars())
    if db.execute(select(func.min(TraceMessage.first_seen))).scalar_one_or_none() is None:
        return []
    edge = now - timedelta(days=monthly.TRACE_DAYS)
    m = monthly.month_of(edge)
    if monthly.bounds(m)[0] < edge:
        m = monthly.add_months(m, 1)
    done = []
    while m < current:
        if m not in stored:
            store_month(db, m, now)
            done.append(m)
        m = monthly.add_months(m, 1)
    return done


def run(db: Session, now: datetime | None = None) -> list[str]:
    """Hourly: measure today, freeze finished months. Returns the months frozen."""
    now = now or datetime.now(timezone.utc)
    snapshot(db, now.astimezone(monthly.tz()).date())
    db.commit()
    done = [m.strftime("%Y-%m") for m in store_finished(db, now)]
    db.commit()
    return done


# --- the overview -------------------------------------------------------------------

@dataclass
class DomainUsage:
    domain: str
    customer_id: int | None
    customer: str
    mailboxes: int = 0
    archive_bytes: int = 0
    archive_messages: int = 0
    mail_in: int = 0
    spam_virus: int = 0
    mail_out: int = 0


@dataclass
class Line:
    code: str  # domains, mailboxes, mail_in, mail_out, archive, fixed, minimum, discount
    quantity: Decimal
    price: Decimal
    amount: Decimal


@dataclass
class CustomerUsage:
    id: int | None
    name: str
    ref: str
    reseller_id: int | None
    reseller: str
    reseller_ref: str
    discount_percent: Decimal
    tariffs: dict[str, Any]
    domains: list[DomainUsage] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    trend: list[int] = field(default_factory=list)  # mailboxes, last 12 months

    def total(self, key: str) -> int:
        return sum(getattr(d, key) for d in self.domains)

    @property
    def amount(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), Decimal(0))


@dataclass
class Usage:
    month: date
    current: bool
    stored: bool
    currency: str
    customers: list[CustomerUsage]
    domains: list[DomainUsage]  # all visible domains, incl. those without customer
    can_recount: bool = False


def gb(n: int) -> Decimal:
    return (Decimal(n) / GB).quantize(CENT, ROUND_HALF_UP)


def price_lines(c: CustomerUsage) -> list[Line]:
    t = c.tariffs
    lines: list[Line] = []
    if t.get("fixed") not in (None, ""):
        fx = dec(t["fixed"])
        lines.append(Line("fixed", Decimal(1), fx, fx))
    else:
        qty = {
            "domains": Decimal(len(c.domains)),
            "mailboxes": Decimal(c.total("mailboxes")),
            "mail_in": Decimal(c.total("mail_in")) / 1000,
            "mail_out": Decimal(c.total("mail_out")) / 1000,
            "archive": gb(c.total("archive_bytes")),
        }
        prices = {"domains": "per_domain", "mailboxes": "per_mailbox", "mail_in": "per_1000_in",
                  "mail_out": "per_1000_out", "archive": "per_gb_archive"}
        for code, key in prices.items():
            p = dec(t.get(key))
            if p:
                lines.append(Line(code, qty[code], p, (qty[code] * p).quantize(CENT, ROUND_HALF_UP)))
        sub = sum((ln.amount for ln in lines), Decimal(0))
        minimum = dec(t.get("minimum"))
        if minimum and sub < minimum:
            lines.append(Line("minimum", Decimal(1), minimum - sub, minimum - sub))
    disc = dec(c.discount_percent)
    if disc:
        sub = sum((ln.amount for ln in lines), Decimal(0))
        cut = -(sub * disc / 100).quantize(CENT, ROUND_HALF_UP)
        lines.append(Line("discount", disc, cut, cut))
    return lines


def _mail_counts(db: Session, m: date, rows: list[DomainUsage], now: datetime) -> None:
    stats = monthly.stats_for(db, m, [r.domain for r in rows], now)
    for r in rows:
        s = stats.get(r.domain)
        if s:
            r.mail_in, r.spam_virus, r.mail_out = s.received, s.spam + s.virus, s.outbound


def _trends(db: Session, m: date, rows: list[DomainUsage], current: dict[int | None, int]) -> dict[int | None, list[int]]:
    """Mailboxes per customer for the 12 months up to m (frozen months; the month itself as shown)."""
    first = monthly.add_months(m, -11)
    months = [monthly.add_months(first, i) for i in range(12)]
    ids = {r.customer_id for r in rows if r.customer_id is not None}
    out = {cid: [0] * 12 for cid in ids}
    if ids:
        for mo, cid, n in db.execute(
                select(UsageMonth.month, UsageMonth.customer_id, func.sum(UsageMonth.mailboxes))
                .where(UsageMonth.month >= first, UsageMonth.month < m, UsageMonth.customer_id.in_(ids))
                .group_by(UsageMonth.month, UsageMonth.customer_id)):
            out[cid][months.index(mo)] = int(n)
    for cid in ids:
        out[cid][-1] = current.get(cid, 0)
    return out


def build(db: Session, m: date, scope: Scope, now: datetime | None = None) -> Usage:
    now = now or datetime.now(timezone.utc)
    general = load_tariffs(db)
    frozen = list(db.execute(select(UsageMonth).where(UsageMonth.month == m)).scalars())
    stored = bool(frozen) and month_done(m, now)
    custs: dict[int | None, CustomerUsage] = {}
    if stored:
        rows = [DomainUsage(r.domain, r.customer_id, r.customer, r.mailboxes, r.archive_bytes, r.archive_messages)
                for r in frozen]
        for c in db.execute(select(UsageCustomerMonth).where(UsageCustomerMonth.month == m)).scalars():
            custs[c.customer_id] = CustomerUsage(c.customer_id, c.customer, c.ref, c.reseller_id, c.reseller, c.reseller_ref,
                                                 dec(c.discount_percent), dict(c.tariffs or {}))
    else:
        # A finished month the trace no longer covers and that was never frozen: nothing reliable to show.
        doms = {} if month_done(m, now) and not trace_covers(m, now) else _month_domains(db, m, now)
        live = {c.id: c for c in db.execute(select(BillingCustomer)).scalars()}
        boxes = count_mailboxes(db, m, list(doms))
        arch = _archive_peak(db, m)
        rows = []
        for name, cid in sorted(doms.items()):
            c = live.get(cid) if cid is not None else None
            b, n = arch.get(name, (0, 0))
            rows.append(DomainUsage(name, c.id if c else None, c.name if c else "", boxes.get(name, 0), b, n))
        for c in live.values():
            r = c.reseller
            custs[c.id] = CustomerUsage(c.id, c.name, c.ref, r.id if r else None, r.name if r else "", r.ref if r else "",
                                        dec(r.discount_percent) if r else Decimal(0), effective(general, c.tariffs))

    # Scope: which domains and customers this user sees.
    if scope.reseller_id is not None:
        allowed = {cid for cid, c in custs.items() if c.reseller_id == scope.reseller_id}
        rows = [r for r in rows if r.customer_id in allowed]
    elif scope.domains is not None:
        rows = [r for r in rows if r.domain in scope.domains]
    _mail_counts(db, m, rows, now)

    by_cust: dict[int | None, list[DomainUsage]] = {}
    for r in rows:
        if r.customer_id is not None and r.customer_id in custs:
            by_cust.setdefault(r.customer_id, []).append(r)
        else:
            r.customer_id, r.customer = None, ""
    current = {cid: sum(d.mailboxes for d in ds) for cid, ds in by_cust.items()}
    trends = _trends(db, m, rows, current)
    out: list[CustomerUsage] = []
    for cid, ds in by_cust.items():
        c = custs[cid]
        c.domains = sorted(ds, key=lambda d: d.domain)
        c.lines = price_lines(c) if scope.prices else []
        c.trend = trends.get(cid, [])
        out.append(c)
    if scope.all:  # customers without any domain this month still show (amount: minimum/fixed)
        for cid, c in custs.items():
            if cid not in by_cust and not stored:
                c.lines = price_lines(c) if (c.tariffs.get("fixed") not in (None, "")) else []
                c.trend = [0] * 12
                out.append(c)
    out.sort(key=lambda c: c.name.lower())
    return Usage(month=m, current=m == monthly.month_of(now), stored=stored, currency=str(general.get("currency", "EUR")),
                 customers=out, domains=sorted(rows, key=lambda r: r.domain),
                 can_recount=month_done(m, now) and scope.all)


# --- CSV ----------------------------------------------------------------------------

HEADERS = {
    "en": {
        "month": "month", "ref": "customer_number", "customer": "customer", "reseller": "reseller", "reseller_ref": "reseller_number",
        "domains": "domains", "domain": "domain", "mailboxes": "active_mailboxes", "mail_in": "mail_in", "spam_virus": "spam_virus",
        "mail_out": "mail_out", "archive_gb": "archive_gb", "archive_messages": "archive_messages", "amount": "amount_excl_vat",
        "currency": "currency", "description": "description", "quantity": "quantity", "price": "price",
    },
    "nl": {
        "month": "maand", "ref": "debiteurnummer", "customer": "klant", "reseller": "reseller", "reseller_ref": "reseller_debiteurnummer",
        "domains": "domeinen", "domain": "domein", "mailboxes": "actieve_mailboxen", "mail_in": "mail_in", "spam_virus": "spam_virus",
        "mail_out": "mail_uit", "archive_gb": "archief_gb", "archive_messages": "archief_berichten", "amount": "bedrag_excl_btw",
        "currency": "valuta", "description": "omschrijving", "quantity": "aantal", "price": "tarief",
    },
}
LINE_TEXT = {
    "en": {"domains": "Domains", "mailboxes": "Active mailboxes", "mail_in": "Mail in (per 1,000)", "mail_out": "Mail out (per 1,000)",
           "archive": "Archive (GB)", "fixed": "Fixed price", "minimum": "Top-up to minimum", "discount": "Reseller discount {pct}%"},
    "nl": {"domains": "Domeinen", "mailboxes": "Actieve mailboxen", "mail_in": "Mail in (per 1.000)", "mail_out": "Mail uit (per 1.000)",
           "archive": "Archief (GB)", "fixed": "Vaste prijs", "minimum": "Aanvulling tot minimum", "discount": "Resellerkorting {pct}%"},
}


def line_text(ln: Line, lang: str) -> str:
    t = LINE_TEXT.get(lang, LINE_TEXT["en"])[ln.code]
    return t.format(pct=fmt_num(ln.quantity, ".")) if ln.code == "discount" else t


def fmt_num(v: Decimal | int, decimal_sep: str) -> str:
    if isinstance(v, int):
        return str(v)
    s = format(v.normalize(), "f") if v == v.to_integral() else format(v, "f")
    return s.replace(".", decimal_sep)


def to_csv(u: Usage, kind: str, lang: str = "en", sep: str = ";", prices: bool = True) -> str:
    """kind: customers, domains or lines. With ";" numbers get a decimal comma (Dutch Excel)."""
    h = HEADERS.get(lang, HEADERS["en"])
    dsep = "," if sep == ";" else "."
    num = lambda v: fmt_num(v, dsep)  # noqa: E731
    money = lambda v: format(Decimal(v).quantize(CENT, ROUND_HALF_UP), "f").replace(".", dsep)  # noqa: E731
    month = u.month.strftime("%Y-%m")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n")
    if kind == "domains":
        w.writerow([h[k] for k in ("month", "domain", "ref", "customer", "reseller", "mailboxes", "mail_in", "spam_virus",
                                   "mail_out", "archive_gb", "archive_messages")])
        custs = {c.id: c for c in u.customers}
        for d in u.domains:
            c = custs.get(d.customer_id)
            w.writerow([month, d.domain, c.ref if c else "", d.customer, c.reseller if c else "", d.mailboxes, d.mail_in,
                        d.spam_virus, d.mail_out, money(gb(d.archive_bytes)), d.archive_messages])
    elif kind == "lines":
        w.writerow([h[k] for k in ("month", "ref", "customer", "reseller", "reseller_ref", "description", "quantity", "price",
                                   "amount", "currency")])
        for c in u.customers:
            for ln in c.lines:
                w.writerow([month, c.ref, c.name, c.reseller, c.reseller_ref, line_text(ln, lang),
                            num(ln.quantity.quantize(Decimal("0.001")).normalize() if ln.code in ("mail_in", "mail_out") else ln.quantity),
                            money(ln.price), money(ln.amount), u.currency])
    else:
        cols = ["month", "ref", "customer", "reseller", "reseller_ref", "domains", "mailboxes", "mail_in", "spam_virus",
                "mail_out", "archive_gb"] + (["amount", "currency"] if prices else [])
        w.writerow([h[k] for k in cols])
        for c in u.customers:
            row = [month, c.ref, c.name, c.reseller, c.reseller_ref, len(c.domains), c.total("mailboxes"), c.total("mail_in"),
                   c.total("spam_virus"), c.total("mail_out"), money(gb(c.total("archive_bytes")))]
            if prices:
                row += [money(c.amount), u.currency]
            w.writerow(row)
    return "﻿" + buf.getvalue()  # BOM: Excel opens UTF-8 correctly
