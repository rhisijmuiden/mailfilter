"""Monthly report per customer: how much mail came in for their domains and how
much spam and how many viruses Mailfilter stopped, as an e-mail on the first
days of the month and as a page in the web interface.

The mail trace keeps 90 days, so the numbers of every finished month are
written to ``monthly_stats`` (one row per domain and month). Months that are
not stored yet (the current month, or a month just after installing) are
counted from the trace on the fly.

``mailfilter-admin monthly-report`` runs every hour from a systemd timer. It
stores the finished months and, when the report is switched on and its day
and hour have come, mails last month's report to every customer user (and,
when asked, an overview of all customers to the admins).
"""

from __future__ import annotations

import html
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from . import branding, mailer
from .api.dashboard import category
from .models import Domain, MonthlyReportUser, MonthlyStats, TraceMessage, TraceRecipient, User
from .settings import get_settings
from .settings_store import get_setting, set_setting

SETTING_KEY = "monthly_report"
LAST_RUN_KEY = "monthly_report.last_run"  # "YYYY-MM" of the report month last sent
VIRUS_SYMBOL = "CLAM_VIRUS"
# Months that started longer ago than this are no longer complete in the trace.
TRACE_DAYS = 85
HISTORY_MONTHS = 12


@dataclass
class MonthlyConfig:
    enabled: bool = False
    day: int = 1  # day of the month the report goes out (1-28)
    hour: int = 8  # local time (Settings.timezone)
    admin_summary: bool = True  # admins get one overview of all customers


def load_config(db: Session) -> MonthlyConfig:
    data = get_setting(db, SETTING_KEY) or {}
    known = MonthlyConfig.__dataclass_fields__
    return MonthlyConfig(**{k: v for k, v in data.items() if k in known})


def save_config(db: Session, cfg: MonthlyConfig) -> None:
    set_setting(db, SETTING_KEY, asdict(cfg))


# --- months -------------------------------------------------------------------

def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def month_of(d: date | datetime) -> date:
    if isinstance(d, datetime):
        d = d.astimezone(tz()).date()
    return d.replace(day=1)


def add_months(m: date, n: int) -> date:
    y, mo = divmod(m.year * 12 + m.month - 1 + n, 12)
    return date(y, mo + 1, 1)


def bounds(m: date) -> tuple[datetime, datetime]:
    """Start and end of a month in local time, as UTC datetimes."""
    zone = tz()
    start = datetime.combine(m, time(), zone)
    end = datetime.combine(add_months(m, 1), time(), zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def parse_month(text: str) -> date:
    """"2026-10" -> date(2026, 10, 1); raises ValueError."""
    y, m = text.split("-", 1)
    return date(int(y), int(m), 1)


# --- counting -----------------------------------------------------------------

COUNTS = ("received", "delivered", "spam", "virus", "refused", "other", "outbound", "outbound_blocked")


@dataclass
class Stats:
    received: int = 0
    delivered: int = 0
    spam: int = 0
    virus: int = 0
    refused: int = 0
    other: int = 0
    outbound: int = 0
    outbound_blocked: int = 0

    def add(self, other: Stats) -> Stats:
        for k in COUNTS:
            setattr(self, k, getattr(self, k) + getattr(other, k))
        return self

    @property
    def stopped(self) -> int:
        return self.spam + self.virus

    @property
    def stopped_percent(self) -> float:
        return round(100 * self.stopped / self.received, 1) if self.received else 0.0

    def to_dict(self) -> dict:
        return {**asdict(self), "stopped": self.stopped, "stopped_percent": self.stopped_percent}


def count(db: Session, m: date, domains: list[str]) -> dict[str, Stats]:
    """Count one month from the trace, per domain. Incoming mail counts for the
    domain of each recipient (once per message), outgoing mail for the sender's domain."""
    names = sorted({d.lower() for d in domains})
    out = {d: Stats() for d in names}
    if not names:
        return out
    start, end = bounds(m)
    virus = TraceMessage.symbols.has_key(VIRUS_SYMBOL)  # noqa: W601 (JSONB ? operator)
    msg = select(
        TraceMessage.queue_id, TraceMessage.direction, TraceMessage.sender,
        category().label("cat"), virus.label("virus"),
    ).where(TraceMessage.first_seen >= start, TraceMessage.first_seen < end).subquery()

    rdom = func.lower(func.split_part(TraceRecipient.address, "@", 2))
    inbound = db.execute(
        select(rdom, msg.c.cat, msg.c.virus, func.count(distinct(msg.c.queue_id)))
        .select_from(msg.join(TraceRecipient, TraceRecipient.queue_id == msg.c.queue_id))
        .where(msg.c.direction.is_distinct_from("outbound"), rdom.in_(names))
        .group_by(rdom, msg.c.cat, msg.c.virus)
    )
    for dom, cat, is_virus, n in inbound:
        s = out[dom]
        s.received += n
        if is_virus and cat != "delivered":
            s.virus += n
        elif cat == "delivered":
            s.delivered += n
        elif cat == "spam":
            s.spam += n
        elif cat == "refused":
            s.refused += n
        else:
            s.other += n

    sdom = func.lower(func.split_part(msg.c.sender, "@", 2))
    outbound = db.execute(
        select(sdom, msg.c.cat, msg.c.virus, func.count())
        .where(msg.c.direction == "outbound", sdom.in_(names))
        .group_by(sdom, msg.c.cat, msg.c.virus)
    )
    for dom, cat, is_virus, n in outbound:
        s = out[dom]
        s.outbound += n
        if cat == "spam" or (is_virus and cat != "delivered"):
            s.outbound_blocked += n
    return out


def hosted_domains(db: Session) -> list[str]:
    return [n.lower() for n in db.execute(select(Domain.name).order_by(Domain.name)).scalars()]


def store_month(db: Session, m: date) -> int:
    """Write the counts of a finished month for every domain; returns the rows written."""
    counted = count(db, m, hosted_domains(db))
    have = {r.domain: r for r in db.execute(select(MonthlyStats).where(MonthlyStats.month == m)).scalars()}
    for dom, s in counted.items():
        row = have.get(dom) or MonthlyStats(month=m, domain=dom)
        for k in COUNTS:
            setattr(row, k, getattr(s, k))
        db.add(row)
    db.flush()
    return len(counted)


def store_finished(db: Session, now: datetime | None = None) -> list[date]:
    """Store every finished month that is still complete in the trace and not stored yet."""
    now = now or datetime.now(timezone.utc)
    current = month_of(now)
    stored = set(db.execute(select(MonthlyStats.month).distinct()).scalars())
    oldest = db.execute(select(func.min(TraceMessage.first_seen))).scalar_one_or_none()
    if oldest is None:
        return []
    done = []
    # Only months that started inside the trace window are complete: an older
    # month may have lost its first days already (the trace keeps 90 days).
    edge = now - timedelta(days=TRACE_DAYS)
    m = month_of(edge)
    if bounds(m)[0] < edge:
        m = add_months(m, 1)
    while m < current:
        if m not in stored:
            store_month(db, m)
            done.append(m)
        m = add_months(m, 1)
    return done


def stats_for(db: Session, m: date, domains: list[str], now: datetime | None = None) -> dict[str, Stats]:
    """Counts per domain for a month: stored when available, otherwise from the trace
    (only while the trace still covers that month)."""
    names = sorted({d.lower() for d in domains})
    rows = {r.domain: r for r in db.execute(
        select(MonthlyStats).where(MonthlyStats.month == m, MonthlyStats.domain.in_(names))).scalars()}
    stored_month = db.execute(select(func.count()).select_from(MonthlyStats).where(MonthlyStats.month == m)).scalar_one() > 0
    if stored_month:
        return {d: Stats(**{k: getattr(rows[d], k) for k in COUNTS}) if d in rows else Stats() for d in names}
    now = now or datetime.now(timezone.utc)
    if bounds(m)[1] < now - timedelta(days=TRACE_DAYS) or m > month_of(now):
        return {d: Stats() for d in names}  # gone from the trace, or still to come
    return count(db, m, names)


def total(per_domain: dict[str, Stats]) -> Stats:
    t = Stats()
    for s in per_domain.values():
        t.add(s)
    return t


def history(db: Session, domains: list[str], until: date, months: int = HISTORY_MONTHS) -> list[tuple[date, Stats]]:
    """Totals per month for the last ``months`` months up to and including ``until``.
    Stored months come from one query; the rest is counted from the trace."""
    names = sorted({d.lower() for d in domains})
    first = add_months(until, -(months - 1))
    stored_months = set(db.execute(select(MonthlyStats.month).distinct().where(
        MonthlyStats.month >= first, MonthlyStats.month <= until)).scalars())
    sums: dict[date, Stats] = {}
    if names and stored_months:
        cols = [func.coalesce(func.sum(getattr(MonthlyStats, k)), 0) for k in COUNTS]
        for row in db.execute(select(MonthlyStats.month, *cols).where(
                MonthlyStats.month.in_(stored_months), MonthlyStats.domain.in_(names)).group_by(MonthlyStats.month)):
            sums[row[0]] = Stats(*[int(v) for v in row[1:]])
    out = []
    m = first
    while m <= until:
        if m in stored_months:
            out.append((m, sums.get(m, Stats())))
        else:
            out.append((m, total(stats_for(db, m, names))))
        m = add_months(m, 1)
    return out


# --- who gets which report -----------------------------------------------------

def customers(db: Session) -> list[User]:
    return list(db.execute(
        select(User).where(User.role == "customer", User.is_active.is_(True)).order_by(User.email)).scalars())


def user_pref(db: Session, user_id: int) -> MonthlyReportUser | None:
    return db.get(MonthlyReportUser, user_id)


def wants_report(db: Session, user: User) -> bool:
    pref = user_pref(db, user.id)
    return pref is None or pref.enabled


@dataclass
class Report:
    month: date
    title: str  # customer name or "all customers"
    domains: dict[str, Stats]
    previous: Stats
    customers: list[tuple[str, Stats]] = field(default_factory=list)  # admin overview only

    @property
    def total(self) -> Stats:
        return total(self.domains)


def build(db: Session, m: date, domains: list[str], title: str, now: datetime | None = None) -> Report:
    return Report(month=m, title=title, domains=stats_for(db, m, domains, now),
                  previous=total(stats_for(db, add_months(m, -1), domains, now)))


def build_overview(db: Session, m: date, now: datetime | None = None) -> Report:
    rep = build(db, m, hosted_domains(db), "", now)
    for u in customers(db):
        if u.domain_names:
            rep.customers.append((u.name or u.email, total({d: rep.domains.get(d.lower(), Stats()) for d in u.domain_names})))
    rep.customers.sort(key=lambda c: -c[1].received)
    return rep


# --- the e-mail ----------------------------------------------------------------

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "nl": ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"],
}

TEXT = {
    "en": {
        "subject": "Your mail report for {month}",
        "subject_all": "Mail report for all customers, {month}",
        "hello": "Hello {name},",
        "intro": "This is what Mailfilter did for {domains} in {month}.",
        "intro_all": "This is what Mailfilter did for all domains in {month}.",
        "received": "Mail received", "delivered": "Delivered to your mailboxes", "spam": "Spam stopped",
        "virus": "Viruses stopped", "refused": "Refused (e.g. unknown address)", "outbound": "Mail sent",
        "outbound_blocked": "Outgoing spam stopped",
        "stopped_line": "{percent}% of the incoming mail was spam or contained a virus and never reached an inbox.",
        "vs": "{sign}{change}% compared to {month}", "same": "same as {month}",
        "per_domain": "Per domain", "per_customer": "Per customer", "domain": "Domain", "customer": "Customer",
        "nothing": "No mail was seen for these domains this month.",
        "footer": "You get this report once a month. You can switch it off under Monthly report in Mailfilter.",
    },
    "nl": {
        "subject": "Je mailrapport over {month}",
        "subject_all": "Mailrapport van alle klanten, {month}",
        "hello": "Hallo {name},",
        "intro": "Dit heeft Mailfilter in {month} gedaan voor {domains}.",
        "intro_all": "Dit heeft Mailfilter in {month} gedaan voor alle domeinen.",
        "received": "Mail ontvangen", "delivered": "Afgeleverd in je mailboxen", "spam": "Spam tegengehouden",
        "virus": "Virussen tegengehouden", "refused": "Geweigerd (bijv. onbekend adres)", "outbound": "Mail verstuurd",
        "outbound_blocked": "Uitgaande spam tegengehouden",
        "stopped_line": "{percent}% van de binnenkomende mail was spam of bevatte een virus en kwam nooit in een inbox.",
        "vs": "{sign}{change}% ten opzichte van {month}", "same": "gelijk aan {month}",
        "per_domain": "Per domein", "per_customer": "Per klant", "domain": "Domein", "customer": "Klant",
        "nothing": "Er is deze maand geen mail gezien voor deze domeinen.",
        "footer": "Je krijgt dit rapport één keer per maand. Je kunt het uitzetten onder Maandrapport in Mailfilter.",
    },
}

# House style (rhis-huisstijl): nacht, koraal, wolk.
NACHT, KORAAL, WOLK, MUTED, LINE = "#14213D", "#FF6B57", "#F6F7FB", "#4F5B73", "#DCE1EB"
GOOD, BAD = "#1F8A5B", "#B8392A"


def month_name(m: date, lang: str) -> str:
    return f"{MONTHS.get(lang, MONTHS['en'])[m.month - 1]} {m.year}"


def fmt(n: int | float, lang: str) -> str:
    """1234 -> "1,234" (en) or "1.234" (nl); 12.5 -> "12.5" or "12,5"."""
    s = f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"
    return s.translate(str.maketrans(",.", ".,")) if lang == "nl" else s


def change(now: int, before: int, m: date, lang: str) -> str:
    t = TEXT.get(lang, TEXT["en"])
    prev = month_name(add_months(m, -1), lang)
    if not before:
        return ""
    pct = round(100 * (now - before) / before)
    if pct == 0:
        return t["same"].format(month=prev)
    return t["vs"].format(sign="+" if pct > 0 else "−", change=abs(pct), month=prev)


def render(rep: Report, lang: str = "en", name: str = "", brand: dict | None = None) -> tuple[str, str, str]:
    """Return (subject, plain text, html). ``brand``: see branding.get."""
    brand = brand or branding.DEFAULTS
    t = TEXT.get(lang, TEXT["en"])
    month = month_name(rep.month, lang)
    tot, overview = rep.total, bool(rep.customers) or not rep.title
    subject = (t["subject_all"] if overview else t["subject"]).format(month=month)
    intro = t["intro_all"].format(month=month) if overview else t["intro"].format(
        month=month, domains=", ".join(rep.domains) or "-")
    tiles = [("received", tot.received, rep.previous.received), ("spam", tot.spam, rep.previous.spam),
             ("virus", tot.virus, rep.previous.virus)]
    details = [("delivered", tot.delivered), ("refused", tot.refused), ("outbound", tot.outbound),
               ("outbound_blocked", tot.outbound_blocked)]

    lines = [t["hello"].format(name=name).replace(" ,", ","), "", intro, ""]
    for key, n, before in tiles:
        ch = change(n, before, rep.month, lang)
        lines.append(f"{t[key]}: {fmt(n, lang)}" + (f" ({ch})" if ch else ""))
    for key, n in details:
        lines.append(f"{t[key]}: {fmt(n, lang)}")
    lines.append("")
    if tot.received:
        lines += [t["stopped_line"].format(percent=fmt(tot.stopped_percent, lang)), ""]
    elif not tot.outbound:
        lines += [t["nothing"], ""]
    table = rep.customers if rep.customers else (list(rep.domains.items()) if len(rep.domains) > 1 else [])
    if table:
        lines.append(t["per_customer"] if rep.customers else t["per_domain"])
        for label, s in table:
            lines.append(f"  {label}: {t['received'].lower()} {fmt(s.received, lang)}, {t['spam'].lower()} {fmt(s.spam, lang)}, "
                         f"{t['virus'].lower()} {fmt(s.virus, lang)}")
        lines.append("")
    lines.append(t["footer"])
    text = "\n".join(lines)

    e = html.escape
    tile_html = []
    for key, n, before in tiles:
        ch = change(n, before, rep.month, lang)
        accent = KORAAL if key != "received" else NACHT
        tile_html.append(
            f'<td width="33%" valign="top" style="padding:6px">'
            f'<div style="border:1px solid {LINE};border-radius:10px;padding:14px 12px;background:#FFFFFF">'
            f'<div style="font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.04em">{e(t[key])}</div>'
            f'<div style="font-size:28px;font-weight:700;color:{accent};margin-top:4px">{e(fmt(n, lang))}</div>'
            f'<div style="font-size:11.5px;color:{MUTED};margin-top:2px;min-height:14px">{e(ch)}</div></div></td>'
        )
    bar = ""
    if tot.received:
        parts = [(tot.delivered, "#3E7BFA"), (tot.spam, KORAAL), (tot.virus, BAD), (tot.refused + tot.other, "#A9B2C3")]
        cells = "".join(
            f'<td width="{max(1, round(100 * n / tot.received))}%" style="background:{c};height:10px;font-size:0;line-height:0">&nbsp;</td>'
            for n, c in parts if n)
        bar = (f'<tr><td style="padding:4px 22px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
               f'style="border-radius:6px;overflow:hidden"><tr>{cells}</tr></table>'
               f'<p style="font-size:14px;line-height:1.5;color:{NACHT};margin:12px 0 0">'
               f'<b>{e(t["stopped_line"].format(percent=fmt(tot.stopped_percent, lang)))}</b></p></td></tr>')
    elif not tot.outbound:
        bar = f'<tr><td style="padding:4px 22px 0;color:{MUTED};font-size:14px">{e(t["nothing"])}</td></tr>'
    detail_rows = "".join(
        f'<tr><td style="padding:7px 0;border-top:1px solid {LINE};color:{MUTED}">{e(t[k])}</td>'
        f'<td align="right" style="padding:7px 0;border-top:1px solid {LINE};font-weight:600">{e(fmt(n, lang))}</td></tr>'
        for k, n in details)
    table_html = ""
    if table:
        head = t["customer"] if rep.customers else t["domain"]
        th = f'style="text-align:right;padding:8px 0 6px;font-size:12px;color:{MUTED};font-weight:600"'
        rows = "".join(
            f'<tr><td style="padding:7px 0;border-top:1px solid {LINE}">{e(label)}</td>'
            f'<td align="right" style="padding:7px 0;border-top:1px solid {LINE}">{e(fmt(s.received, lang))}</td>'
            f'<td align="right" style="padding:7px 0;border-top:1px solid {LINE}">{e(fmt(s.spam, lang))}</td>'
            f'<td align="right" style="padding:7px 0;border-top:1px solid {LINE}">{e(fmt(s.virus, lang))}</td></tr>'
            for label, s in table)
        table_html = (
            f'<tr><td style="padding:18px 22px 0"><div style="font-size:15px;font-weight:700">'
            f'{e(t["per_customer"] if rep.customers else t["per_domain"])}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:13.5px;margin-top:6px">'
            f'<tr><td style="padding:8px 0 6px;font-size:12px;color:{MUTED};font-weight:600">{e(head)}</td>'
            f'<td {th}>{e(t["received"])}</td><td {th}>{e(t["spam"])}</td><td {th}>{e(t["virus"])}</td></tr>{rows}</table></td></tr>')
    greeting = e(t["hello"].format(name=name).replace(" ,", ","))
    body = (
        f'<!doctype html><html><body style="margin:0;background:{WOLK};font-family:Archivo,Segoe UI,Arial,sans-serif;color:{NACHT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{WOLK}"><tr><td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#FFFFFF;border:1px solid {LINE};border-radius:12px;overflow:hidden">'
        f'<tr><td style="background:{brand["primary"]};color:{branding.on_color(brand["primary"])};padding:18px 22px;font-size:18px;font-weight:700">'
        f'<span style="color:{brand["accent"]}">●</span> {e(brand["name"])}<span style="float:right;font-weight:400;font-size:14px;opacity:.8">{e(month)}</span></td></tr>'
        f'<tr><td style="padding:22px 22px 6px"><div style="font-size:22px;font-weight:700">{e(subject)}</div>'
        f'<p style="font-size:14px;line-height:1.5;color:{MUTED};margin:10px 0 0">{greeting}<br>{e(intro)}</p></td></tr>'
        f'<tr><td style="padding:10px 16px 6px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{"".join(tile_html)}</tr></table></td></tr>'
        f'{bar}'
        f'<tr><td style="padding:16px 22px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:13.5px">{detail_rows}</table></td></tr>'
        f'{table_html}'
        f'<tr><td style="padding:22px;font-size:12px;color:{MUTED}">{e(t["footer"])}</td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return subject, text, body


# --- sending -------------------------------------------------------------------

@dataclass
class RunResult:
    ran: bool
    reason: str = ""
    month: str = ""
    stored: list[str] = field(default_factory=list)
    sent: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


def due(cfg: MonthlyConfig, last_run: str | None, now: datetime) -> bool:
    local = now.astimezone(tz())
    report_month = add_months(local.date().replace(day=1), -1).strftime("%Y-%m")
    return cfg.enabled and last_run != report_month and (local.day, local.hour) >= (cfg.day, cfg.hour)


def send_customer(db: Session, user: User, m: date) -> None:
    rep = build(db, m, user.domain_names, user.name or user.email)
    subject, text, body = render(rep, user.language, user.name, brand=branding.get(db))
    mailer.send(db, user.email, subject, text, body)


def send_overview(db: Session, user: User, m: date) -> None:
    rep = build_overview(db, m)
    subject, text, body = render(rep, user.language, user.name, brand=branding.get(db))
    mailer.send(db, user.email, subject, text, body)


def run(db: Session, now: datetime | None = None, force: bool = False) -> RunResult:
    """Store finished months, then send last month's reports when due (or always with ``force``)."""
    now = now or datetime.now(timezone.utc)
    res = RunResult(False)
    res.stored = [m.strftime("%Y-%m") for m in store_finished(db, now)]
    db.commit()
    cfg = load_config(db)
    last_run = get_setting(db, LAST_RUN_KEY)
    if not force and not due(cfg, last_run, now):
        res.reason = "disabled" if not cfg.enabled else "not_due"
        return res
    m = add_months(month_of(now), -1)
    res.ran, res.month = True, m.strftime("%Y-%m")
    for user in customers(db):
        if not user.domain_names or not wants_report(db, user):
            res.skipped += 1
            continue
        pref = user_pref(db, user.id)
        if pref is not None and pref.last_month == m and not force:
            continue  # already sent (e.g. a run that stopped half way)
        try:
            send_customer(db, user, m)
        except mailer.MailError as e:
            res.failed.append(f"{user.email}: {e.code}")
            continue
        if pref is None:
            pref = MonthlyReportUser(user_id=user.id, enabled=True)
            db.add(pref)
        pref.last_month, pref.sent_at = m, now
        db.commit()
        res.sent += 1
    if cfg.admin_summary:
        for admin in db.execute(select(User).where(User.role == "admin", User.is_active.is_(True))).scalars():
            try:
                send_overview(db, admin, m)
                res.sent += 1
            except mailer.MailError as e:
                res.failed.append(f"{admin.email}: {e.code}")
    set_setting(db, LAST_RUN_KEY, res.month)
    db.commit()
    return res
