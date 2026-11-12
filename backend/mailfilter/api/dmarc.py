"""DMARC reports: who sends mail as our domains, does it pass, and the next
step towards p=reject. Customers see only their own domains.

Reports reach Mailfilter by mail (dmarc-reports@<hostname>, see
mailfilter.dmarc.ingest) or as an upload. Publishing the rua address or a
new policy goes through the DNS toolbox (/api/dns/publish, kinds dmarc_rua,
dmarc_policy and dmarc_auth), so it has the same preview and undo.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from mailfilter_dns import dmarc as dmarc_rec
from mailfilter_dns.config import ServerConfig
from mailfilter_dns.resolver import DnsError, DnsResolver, Resolver, norm

from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser, visible_domains
from ..dmarc import rua_address
from ..dmarc.advice import Group, advise
from ..dmarc.ingest import ingest_file
from ..dmarc.senders import identify, normalize_ips
from ..models import DmarcIngest, DmarcReport, DmarcRow, Domain
from ..settings_store import get_secret

router = APIRouter(prefix="/dmarc", tags=["dmarc"])

MAX_UPLOAD = 60 * 1024 * 1024
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


# Swappable in tests ------------------------------------------------------------------

def get_resolver() -> Resolver:
    return DnsResolver(timeout=3.0)


def get_server() -> ServerConfig:
    return ServerConfig.load()


# Helpers -------------------------------------------------------------------------------

def _domains_for(user, db) -> list[str]:
    vis = visible_domains(user)
    if vis is not None:
        return sorted({d.lower() for d in vis})
    names = set(db.execute(select(Domain.name)).scalars())
    names |= set(db.execute(select(DmarcReport.domain).distinct()).scalars())
    return sorted({n.lower() for n in names})


def _check_domain(domain: str, user) -> str:
    d = norm(domain)
    if not DOMAIN_RE.match(d):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_domain")
    vis = visible_domains(user)
    if vis is not None and d not in {x.lower() for x in vis}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return d


def _since(days: int) -> datetime:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1)


def _rows(db, domain: str, since: datetime):
    return db.execute(
        select(DmarcRow, DmarcReport.begin, DmarcReport.org_name, DmarcReport.p)
        .join(DmarcReport, DmarcRow.report_pk == DmarcReport.id)
        .where(DmarcReport.domain == domain, DmarcReport.begin >= since)
    ).all()


def _passed(row: DmarcRow) -> bool:
    return row.dkim == "pass" or row.spf == "pass"


def _forwarded(row: DmarcRow) -> bool:
    return any((r or {}).get("type") in ("forwarded", "mailing_list", "local_policy", "trusted_forwarder")
               for r in row.reasons or [])


def _groups(rows, own_ips: set[str]) -> list[Group]:
    groups: dict[str, Group] = {}
    for row, *_ in rows:
        name, kind = identify(row.source_ip, row.source_host, own_ips)
        g = groups.setdefault(name, Group(name, kind))
        ok = _passed(row)
        g.messages += row.count
        g.passed += row.count if ok else 0
        g.dkim_pass += row.count if row.dkim == "pass" else 0
        g.spf_pass += row.count if row.spf == "pass" else 0
        g.forwarded += row.count if (not ok and _forwarded(row)) else 0
        ip = g.ips.setdefault(row.source_ip, {"ip": row.source_ip, "host": row.source_host, "messages": 0, "passed": 0,
                                              "dkim_pass": 0, "spf_pass": 0, "quarantined": 0, "rejected": 0,
                                              "dkim_domains": set(), "spf_domains": set()})
        ip["messages"] += row.count
        ip["passed"] += row.count if ok else 0
        ip["dkim_pass"] += row.count if row.dkim == "pass" else 0
        ip["spf_pass"] += row.count if row.spf == "pass" else 0
        ip["quarantined"] += row.count if row.disposition == "quarantine" else 0
        ip["rejected"] += row.count if row.disposition == "reject" else 0
        ip["host"] = ip["host"] or row.source_host
        for a in row.dkim_auth or []:
            if a.get("domain"):
                ip["dkim_domains"].add(f"{a['domain']}:{a.get('result', '')}")
        for a in row.spf_auth or []:
            if a.get("domain"):
                ip["spf_domains"].add(f"{a['domain']}:{a.get('result', '')}")
    return sorted(groups.values(), key=lambda g: -g.messages)


def _group_out(g: Group) -> dict:
    ips = sorted(g.ips.values(), key=lambda i: -i["messages"])
    return {
        "name": g.name, "kind": g.kind, "verdict": g.verdict, "messages": g.messages, "passed": g.passed,
        "failed": g.failed, "dkim_pass": g.dkim_pass, "spf_pass": g.spf_pass, "forwarded": g.forwarded,
        "ips": [{**i, "dkim_domains": sorted(i["dkim_domains"]), "spf_domains": sorted(i["spf_domains"])} for i in ips[:50]],
        "more_ips": max(0, len(ips) - 50),
    }


def _summary(db, domains: list[str], since: datetime) -> dict[str, dict]:
    """Per domain totals in one query."""
    if not domains:
        return {}
    passed = func.sum(DmarcRow.count).filter((DmarcRow.dkim == "pass") | (DmarcRow.spf == "pass"))
    q = (select(DmarcReport.domain, func.coalesce(func.sum(DmarcRow.count), 0), func.coalesce(passed, 0),
                func.coalesce(func.sum(DmarcRow.count).filter(DmarcRow.disposition != "none"), 0))
         .join(DmarcRow, DmarcRow.report_pk == DmarcReport.id)
         .where(DmarcReport.domain.in_(domains), DmarcReport.begin >= since).group_by(DmarcReport.domain))
    out = {d: {"messages": int(m), "passed": int(p), "enforced": int(e)} for d, m, p, e in db.execute(q)}
    q2 = (select(DmarcReport.domain, func.count(DmarcReport.id), func.count(func.distinct(DmarcReport.org_name)),
                 func.max(DmarcReport.end))
          .where(DmarcReport.domain.in_(domains), DmarcReport.begin >= since).group_by(DmarcReport.domain))
    for d, n, orgs, last in db.execute(q2):
        out.setdefault(d, {"messages": 0, "passed": 0, "enforced": 0}).update(reports=n, reporters=orgs, last_report=last)
    # The policy the newest report saw (cheap; the live DNS record is on the domain page)
    newest = (select(DmarcReport.domain, func.max(DmarcReport.end).label("end"))
              .where(DmarcReport.domain.in_(domains)).group_by(DmarcReport.domain).subquery())
    latest = (select(DmarcReport.domain, DmarcReport.p, DmarcReport.pct)
              .join(newest, (DmarcReport.domain == newest.c.domain) & (DmarcReport.end == newest.c.end)))
    for d, p, pct in db.execute(latest):
        out.setdefault(d, {"messages": 0, "passed": 0, "enforced": 0}).update(policy=p, pct=pct)
    return out


def _live_record(domain: str) -> tuple[str | None, dict | None, str | None]:
    """(record, tags, error). error is dns_error or syntax."""
    try:
        txts = get_resolver().txt(f"_dmarc.{domain}")
    except DnsError:
        return None, None, "dns_error"
    rec, _ = dmarc_rec.find_dmarc(txts)
    if rec is None:
        return None, None, None
    try:
        return rec, dmarc_rec.parse(rec), None
    except dmarc_rec.DmarcSyntaxError:
        return rec, None, "syntax"


# Endpoints -----------------------------------------------------------------------------

@router.get("/overview")
def overview(user: CurrentUser, db: DB, days: int = Query(30, ge=1, le=400)) -> dict:
    domains = _domains_for(user, db)
    since = _since(days)
    summary = _summary(db, domains, since)
    items = []
    for d in domains:
        s = summary.get(d, {})
        items.append({"domain": d, "messages": s.get("messages", 0), "passed": s.get("passed", 0),
                      "failed": s.get("messages", 0) - s.get("passed", 0), "enforced": s.get("enforced", 0),
                      "reports": s.get("reports", 0), "reporters": s.get("reporters", 0),
                      "last_report": s.get("last_report"), "policy": s.get("policy"), "pct": s.get("pct")})
    items.sort(key=lambda i: (-i["messages"], i["domain"]))
    total = sum(i["messages"] for i in items)
    passed = sum(i["passed"] for i in items)
    server = get_server()
    return {"days": days, "rua": rua_address(server.hostname), "domains": items,
            "totals": {"messages": total, "passed": passed, "failed": total - passed,
                       "domains_with_reports": sum(1 for i in items if i["reports"])}}


@router.get("/domain/{domain}")
def domain_detail(domain: str, user: CurrentUser, db: DB, days: int = Query(30, ge=1, le=400)) -> dict:
    d = _check_domain(domain, user)
    since = _since(days)
    server = get_server()
    own = normalize_ips(server.addresses)
    rows = _rows(db, d, since)
    groups = _groups(rows, own)

    series: dict[date, list[int]] = {(since + timedelta(days=i)).date(): [0, 0] for i in range(days)}
    reporters: dict[str, dict] = defaultdict(lambda: {"reports": set(), "messages": 0})
    policy_days: dict[str, set] = defaultdict(set)
    for row, begin, org, p in rows:
        day = begin.astimezone(timezone.utc).date()
        if day in series:
            series[day][0 if _passed(row) else 1] += row.count
        reporters[org]["reports"].add(row.report_pk)
        reporters[org]["messages"] += row.count
        policy_days[p].add(day)
    messages = sum(g.messages for g in groups)
    passed = sum(g.passed for g in groups)

    record, tags, rec_error = _live_record(d)
    rua = rua_address(server.hostname)
    has_rua = bool(tags) and rua.lower() in dmarc_rec.rua_addresses(tags)
    if rec_error == "dns_error":
        # DNS did not answer: judge by what the newest report saw instead
        last = db.execute(select(DmarcReport).where(DmarcReport.domain == d).order_by(DmarcReport.end.desc()).limit(1)).scalar_one_or_none()
        tags = {"p": last.p or "none", "pct": str(last.pct if last.pct is not None else 100), "sp": last.sp} if last else None
        has_rua = bool(last)
    current_p = (tags or {}).get("p", "none").lower()
    can_publish = user.is_admin and bool(get_secret(db, "cloudflare_token"))
    advice = advise(tags=tags, record_error=rec_error == "syntax", has_our_rua=has_rua, messages=messages,
                    days=len({day for days_ in policy_days.values() for day in days_}),
                    days_at_policy=len(policy_days.get(current_p, set())), groups=groups, can_publish=can_publish)

    reports = db.execute(select(DmarcReport).where(DmarcReport.domain == d, DmarcReport.begin >= since)
                         .order_by(DmarcReport.begin.desc()).limit(50)).scalars().all()
    return {
        "domain": d, "days": days, "rua": rua, "admin": user.is_admin, "cloudflare": can_publish,
        "record": record, "record_error": rec_error, "tags": tags, "has_rua": has_rua,
        "totals": {"messages": messages, "passed": passed, "failed": messages - passed,
                   "dkim_pass": sum(g.dkim_pass for g in groups), "spf_pass": sum(g.spf_pass for g in groups),
                   "enforced": sum(r.count for r, *_ in rows if r.disposition != "none")},
        "advice": advice,
        "series": [{"t": k.isoformat(), "passed": v[0], "failed": v[1]} for k, v in series.items()],
        "senders": [_group_out(g) for g in groups],
        "reporters": sorted(({"name": k, "reports": len(v["reports"]), "messages": v["messages"]} for k, v in reporters.items()),
                            key=lambda r: -r["messages"]),
        "reports": [_report_out(r, with_rows=False) for r in reports],
    }


def _report_out(r: DmarcReport, *, with_rows: bool, own: set[str] | None = None) -> dict:
    out = {"id": r.id, "org_name": r.org_name, "org_email": r.org_email, "report_id": r.report_id, "domain": r.domain,
           "begin": r.begin, "end": r.end, "p": r.p, "sp": r.sp, "pct": r.pct, "adkim": r.adkim, "aspf": r.aspf,
           "messages": r.messages, "source": r.source, "received_at": r.received_at}
    if with_rows:
        out["rows"] = [{"source_ip": x.source_ip, "source_host": x.source_host,
                        "sender": identify(x.source_ip, x.source_host, own or set())[0], "count": x.count,
                        "disposition": x.disposition, "dkim": x.dkim, "spf": x.spf, "header_from": x.header_from,
                        "envelope_from": x.envelope_from, "dkim_auth": x.dkim_auth, "spf_auth": x.spf_auth,
                        "reasons": x.reasons} for x in sorted(r.rows, key=lambda x: -x.count)]
    return out


@router.get("/report/{report_id}")
def report_detail(report_id: int, user: CurrentUser, db: DB) -> dict:
    r = db.get(DmarcReport, report_id)
    vis = visible_domains(user)
    if r is None or (vis is not None and r.domain not in {x.lower() for x in vis}):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _report_out(r, with_rows=True, own=normalize_ips(get_server().addresses))


@router.get("/setup")
def setup(_: AdminUser, db: DB) -> dict:
    """Is everything in place to receive reports? Plus the most recent report mails."""
    server = get_server()
    resolver = get_resolver()
    checks = {}
    try:
        checks["address"] = bool(resolver.a(server.hostname) or resolver.aaaa(server.hostname) or resolver.mx(server.hostname))
    except DnsError:
        checks["address"] = None
    try:
        probe = f"example.com._report._dmarc.{server.hostname}"
        checks["auth"] = any(t.strip().lower().startswith("v=dmarc1") for t in resolver.txt(probe))
    except DnsError:
        checks["auth"] = None
    log = db.execute(select(DmarcIngest).order_by(DmarcIngest.at.desc()).limit(20)).scalars().all()
    return {"rua": rua_address(server.hostname), "hostname": server.hostname, "checks": checks,
            "cloudflare": bool(get_secret(db, "cloudflare_token")),
            "log": [{"at": x.at, "source": x.source, "sender": x.sender, "subject": x.subject, "status": x.status,
                     "detail": x.detail, "domains": x.domains, "reports": x.reports} for x in log]}


@router.post("/upload")
async def upload(request: Request, admin: AdminUser, db: DB, name: str = Query("report.xml", max_length=255)) -> dict:
    """A report file as the raw body: .xml, .xml.gz, .zip or a saved mail (.eml)."""
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty")
    log = ingest_file(db, name, bytes(data), actor=admin.email)
    audit(db, request, admin, "dmarc.upload", name, status=log.status, reports=log.reports)
    db.commit()
    return {"status": log.status, "detail": log.detail, "domains": log.domains, "reports": log.reports}
