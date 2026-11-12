"""Dashboard: counts, charts and alerts about mail flow. Customers only see
their own domains; server health (queue, services) is for admins only."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, exists, func, literal, or_, select

from ..deps import DB, CurrentUser
from ..models import OutboundHold, TraceMessage, TraceRecipient
from ..queuectl import QueueError, summary
from ..settings import get_settings
from .trace import scope

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

CATEGORIES = ("delivered", "spam", "refused", "delayed", "failed")
SPAM_EXPLAIN = ("rejected_spam", "rejected_blocklist")
SERVICES = ("postfix", "rspamd", "clamav-daemon", "redis-server", "postgresql", "unbound", "caddy",
            "mailfilter-tracer", "crowdsec", "nftables")

# Alert thresholds
STUCK_AFTER = timedelta(hours=1)
BOUNCE_MIN = 5
OUTBOUND_MIN = 100
OUTBOUND_FACTOR = 5
DEFERRED_QUEUE_WARN = 200


class Counts(BaseModel):
    total: int = 0
    inbound: int = 0
    outbound: int = 0
    delivered: int = 0
    spam: int = 0
    refused: int = 0
    delayed: int = 0
    failed: int = 0


class Bucket(BaseModel):
    t: datetime
    delivered: int = 0
    spam: int = 0
    refused: int = 0
    delayed: int = 0
    failed: int = 0


class Top(BaseModel):
    name: str
    count: int


class Alert(BaseModel):
    level: Literal["critical", "warning", "info"]
    code: str
    params: dict = {}
    link: str | None = None


class DashboardOut(BaseModel):
    range: Literal["24h", "30d"]
    timezone: str
    direction: Literal["all", "inbound", "outbound"]
    now: Counts
    previous: Counts
    series: list[Bucket]
    top_senders: list[Top]
    top_domains: list[Top]
    queue: dict[str, int] | None
    alerts: list[Alert]


def category():
    """One outcome per message, so the chart's segments add up to the total."""
    spam_rcpt = exists().where(
        TraceRecipient.queue_id == TraceMessage.queue_id, TraceRecipient.explain.in_(SPAM_EXPLAIN)
    )
    return case(
        (TraceMessage.status == "delivered", literal("delivered")),
        (TraceMessage.status == "quarantined", literal("spam")),
        (and_(TraceMessage.status == "rejected", or_(TraceMessage.spam_action == "reject", spam_rcpt)), literal("spam")),
        (TraceMessage.status == "rejected", literal("refused")),
        (TraceMessage.status.in_(("deferred", "queued")), literal("delayed")),
        else_=literal("failed"),
    )


def _window(user, since: datetime, until: datetime, direction: str):
    stmt = select(TraceMessage).where(TraceMessage.first_seen >= since, TraceMessage.first_seen < until)
    if direction != "all":
        stmt = stmt.where(TraceMessage.direction == direction)
    return scope(stmt, user)


def counts(db, user, since: datetime, until: datetime, direction: str) -> Counts:
    sub = _window(user, since, until, direction).with_only_columns(
        TraceMessage.direction.label("dir"), category().label("cat")
    ).subquery()
    c = Counts()
    for d, cat, n in db.execute(select(sub.c.dir, sub.c.cat, func.count()).group_by(sub.c.dir, sub.c.cat)):
        c.total += n
        setattr(c, cat, getattr(c, cat) + n)
        if d == "inbound":
            c.inbound += n
        elif d == "outbound":
            c.outbound += n
    return c


def series(db, user, since: datetime, until: datetime, direction: str, unit: str, tz: str) -> list[Bucket]:
    local = func.timezone(tz, TraceMessage.first_seen)
    bucket = func.date_trunc(unit, local)
    sub = _window(user, since, until, direction).with_only_columns(bucket.label("b"), category().label("cat")).subquery()
    rows = db.execute(select(sub.c.b, sub.c.cat, func.count()).group_by(sub.c.b, sub.c.cat)).all()
    zone = ZoneInfo(tz)
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    start = since.astimezone(zone).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    if unit == "day":
        start = start.replace(hour=0)
    end = until.astimezone(zone).replace(tzinfo=None)
    buckets: dict[datetime, Bucket] = {}
    t = start
    while t < end:
        buckets[t] = Bucket(t=t.replace(tzinfo=zone))
        t += step
    for b, cat, n in rows:
        if b in buckets:
            setattr(buckets[b], cat, n)
    return list(buckets.values())


def top_senders(db, user, since, until, direction, limit=8) -> list[Top]:
    sub = _window(user, since, until, direction).where(TraceMessage.sender.is_not(None), TraceMessage.sender != "") \
        .with_only_columns(func.lower(TraceMessage.sender).label("s")).subquery()
    rows = db.execute(select(sub.c.s, func.count().label("n")).group_by(sub.c.s).order_by(func.count().desc(), sub.c.s).limit(limit))
    return [Top(name=s, count=n) for s, n in rows]


def top_domains(db, user, since, until, direction, limit=8) -> list[Top]:
    msgs = _window(user, since, until, direction).with_only_columns(TraceMessage.queue_id).subquery()
    dom = func.lower(func.split_part(TraceRecipient.address, "@", 2))
    rows = db.execute(
        select(dom.label("d"), func.count().label("n"))
        .where(TraceRecipient.queue_id.in_(select(msgs.c.queue_id)), dom != "")
        .group_by(dom).order_by(func.count().desc(), dom).limit(limit)
    )
    return [Top(name=d, count=n) for d, n in rows]


def alerts(db, user, now: datetime, queue: dict[str, int] | None) -> list[Alert]:
    out: list[Alert] = []
    hour_ago, day_ago = now - STUCK_AFTER, now - timedelta(hours=25)

    # Mail that has been waiting for more than an hour, per destination domain.
    dom = func.lower(func.split_part(TraceRecipient.address, "@", 2))
    stuck_msgs = scope(select(TraceMessage.queue_id).where(
        TraceMessage.first_seen < hour_ago, TraceMessage.first_seen >= now - timedelta(days=5)), user).subquery()
    for d, n, host in db.execute(
        select(dom, func.count(), func.max(TraceRecipient.relay_host))
        .where(TraceRecipient.status == "deferred", TraceRecipient.queue_id.in_(select(stuck_msgs.c.queue_id)))
        .group_by(dom).order_by(func.count().desc()).limit(3)
    ):
        out.append(Alert(level="warning", code="stuck", params={"count": n, "domain": d, "host": host or ""},
                         link=f"/trace?status=deferred&q={d}"))

    # Bounce spike: the last hour bounces clearly more than the day before.
    last = counts(db, user, hour_ago, now, "all")
    before = counts(db, user, day_ago, hour_ago, "all")
    if last.failed >= BOUNCE_MIN and last.total:
        rate = last.failed / last.total
        base = before.failed / before.total if before.total else 0
        if rate >= max(0.2, 3 * base):
            out.append(Alert(level="warning", code="bounce_spike",
                             params={"count": last.failed, "percent": round(rate * 100)}, link="/trace?status=bounced&hours=1"))

    # Outbound spike per sending server or SMTP account: a hacked website or mailbox.
    source = func.coalesce(TraceMessage.sasl_user, func.host(TraceMessage.client_ip), TraceMessage.client_host)
    def per_source(since, until):
        sub = _window(user, since, until, "outbound").with_only_columns(source.label("src")).subquery()
        return dict(db.execute(select(sub.c.src, func.count()).group_by(sub.c.src)).all())
    recent, earlier = per_source(hour_ago, now), per_source(day_ago, hour_ago)
    for src, n in sorted(recent.items(), key=lambda kv: -kv[1]):
        avg = earlier.get(src, 0) / 24
        if src and n >= OUTBOUND_MIN and n >= OUTBOUND_FACTOR * max(avg, 1):
            out.append(Alert(level="critical", code="outbound_spike",
                             params={"source": src, "count": n, "usual": round(avg)}, link=f"/trace?direction=outbound&hours=1&q={src}"))

    if user.is_admin:
        if queue and queue.get("deferred", 0) >= DEFERRED_QUEUE_WARN:
            out.append(Alert(level="warning", code="queue_large", params={"count": queue["deferred"]}, link="/trace?status=deferred"))
        down = services_down()
        if down:
            out.append(Alert(level="critical", code="services_down", params={"services": ", ".join(down)}))

    if user.is_admin:
        paused = list(db.execute(select(OutboundHold.value).where(OutboundHold.released_at.is_(None))
                                 .order_by(OutboundHold.created_at.desc())).scalars())
        if paused:
            out.append(Alert(level="critical", code="paused_senders",
                             params={"count": len(paused), "senders": ", ".join(paused[:3])}, link="/relay"))

    held = db.execute(scope(select(func.count()).select_from(TraceMessage).where(
        TraceMessage.status == "quarantined", TraceMessage.done.is_(False)), user)).scalar_one()
    if held:
        out.append(Alert(level="info", code="quarantine", params={"count": held}, link="/quarantine"))
    return out


def services_down() -> list[str]:
    try:
        res = subprocess.run(["systemctl", "is-active", *SERVICES], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    states = res.stdout.split()
    if len(states) != len(SERVICES):
        return []
    return [s for s, st in zip(SERVICES, states) if st not in ("active", "activating", "reloading")]


@router.get("", response_model=DashboardOut)
def dashboard(
    user: CurrentUser,
    db: DB,
    range_: Literal["24h", "30d"] = Query("24h", alias="range"),
    direction: Literal["all", "inbound", "outbound"] = "all",
) -> DashboardOut:
    tz = get_settings().timezone
    now = datetime.now(timezone.utc)
    span = timedelta(hours=24) if range_ == "24h" else timedelta(days=30)
    unit = "hour" if range_ == "24h" else "day"
    since = now - span
    if unit == "hour":
        since = since.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    queue = None
    if user.is_admin:
        try:
            queue = summary()
        except (QueueError, ValueError):
            queue = None
    return DashboardOut(
        range=range_,
        timezone=tz,
        direction=direction,
        now=counts(db, user, now - span, now, direction),
        previous=counts(db, user, now - 2 * span, now - span, direction),
        series=series(db, user, since, now, direction, unit, tz),
        top_senders=top_senders(db, user, now - span, now, direction),
        top_domains=top_domains(db, user, now - span, now, direction),
        queue=queue,
        alerts=alerts(db, user, now, queue),
    )
