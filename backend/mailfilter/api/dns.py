"""DNS toolbox, Cloudflare publishing, go-live and blocklist status (admin only).

All logic lives in ``mailfilter_dns``; this module only adds login, audit
logging and the Cloudflare token from the encrypted settings store. Changes
to Cloudflare are always re-planned on the server right before they are
applied, so the browser can never send records of its own.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from mailfilter_dns import bimi, blacklist, dkim, dmarc, domaincheck, golive, lookup, publish, spf, tlssec
from mailfilter_dns.cloudflare import Cloudflare, CloudflareError
from mailfilter_dns.config import ServerConfig, relay_domains
from mailfilter_dns.resolver import DnsError, DnsResolver, FreshTxt, Resolver, norm

from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..dmarc import rua_address
from ..settings_store import get_secret, set_secret

router = APIRouter(prefix="/dns", tags=["dns"])
# No login: Caddy asks whether it may get a certificate, and senders fetch MTA-STS policies
public = APIRouter(prefix="/mta-sts", tags=["dns"])
log = logging.getLogger("mailfilter.dns")

TOKEN_KEY = "cloudflare_token"
DOMAIN_RE = r"^(?=.{1,253}$)([a-zA-Z0-9_](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\.?$"


# Swappable in tests -----------------------------------------------------------

def get_resolver() -> Resolver:
    return DnsResolver()


def get_server() -> ServerConfig:
    """This server; in a cluster the shared SPF record lists the addresses of all nodes."""
    server = ServerConfig.load()
    try:
        from ..cluster.state import cluster_server_facts
        from ..db import session

        with session() as db:
            facts = cluster_server_facts(db)
    except Exception:  # noqa: BLE001 - the toolbox must keep working without the database
        facts = None
    if facts:
        server.spf_name = facts["spf_name"] or server.spf_name
        server.public_v4 = list(dict.fromkeys([*server.public_v4, *facts["v4"]]))
        server.public_v6 = list(dict.fromkeys([*server.public_v6, *facts["v6"]]))
    return server


def make_cloudflare(token: str) -> Cloudflare:
    return Cloudflare(token)


def smtp_probe(host: str, ip: str):
    return lookup.default_probe(host, ip)


def fetch_sts_policy(domain: str) -> str:
    import httpx

    r = httpx.get(f"https://mta-sts.{domain}/.well-known/mta-sts.txt", timeout=10, follow_redirects=False)
    r.raise_for_status()
    return r.text


def local_tlsa(host: str) -> str | None:
    return tlssec.local_tlsa(host)


# Helpers ------------------------------------------------------------------------

def _domain(name: str) -> str:
    d = norm(name)
    if not re.match(DOMAIN_RE, d):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_domain")
    return d


def _cf(db) -> Cloudflare:
    token = get_secret(db, TOKEN_KEY)
    if not token:
        raise HTTPException(status.HTTP_409_CONFLICT, "cloudflare_not_configured")
    return make_cloudflare(token)


def _cf_error(exc: CloudflareError) -> HTTPException:
    if exc.status in (401, 403):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, "cloudflare_denied")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"cloudflare_error:{str(exc)[:200]}")


def _zone(cf: Cloudflare, name: str):
    try:
        zone = cf.zone_for(name)
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"zone_not_found:{name}")
    return zone


# Cloudflare token -------------------------------------------------------------

class CloudflareStatus(BaseModel):
    configured: bool
    status: str | None = None  # active, or an error code
    zones: list[str] = []


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.get("/cloudflare", response_model=CloudflareStatus)
def cloudflare_status(_: AdminUser, db: DB) -> CloudflareStatus:
    token = get_secret(db, TOKEN_KEY)
    if not token:
        return CloudflareStatus(configured=False)
    try:
        cf = make_cloudflare(token)
        st = cf.verify_token().get("status", "")
        return CloudflareStatus(configured=True, status=st, zones=[z.name for z in cf.zones()])
    except CloudflareError as exc:
        return CloudflareStatus(configured=True, status="denied" if exc.status in (401, 403) else "unreachable")


@router.put("/cloudflare", response_model=CloudflareStatus)
def cloudflare_save(body: TokenIn, request: Request, user: AdminUser, db: DB) -> CloudflareStatus:
    token = body.token.strip()
    if re.fullmatch(r"[0-9a-f]{37}", token):
        # The Global API Key needs an e-mail address and gives full access; ask for a token
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cloudflare_global_key")
    try:
        cf = make_cloudflare(token)
        st = cf.verify_token().get("status", "")
        zones = [z.name for z in cf.zones()]
    except CloudflareError as exc:
        if exc.status is None:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "cloudflare_unreachable") from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"cloudflare_token_rejected:{str(exc).replace(':', '')[:200]}") from exc
    if st != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"cloudflare_token_rejected:status {st}")
    if not zones:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cloudflare_no_zones")
    set_secret(db, TOKEN_KEY, token)
    audit(db, request, user, "cloudflare.token_set", zones=len(zones))
    db.commit()
    return CloudflareStatus(configured=True, status=st, zones=zones)


@router.delete("/cloudflare", status_code=204)
def cloudflare_remove(request: Request, user: AdminUser, db: DB) -> None:
    set_secret(db, TOKEN_KEY, None)
    audit(db, request, user, "cloudflare.token_removed")
    db.commit()


# Overview of Mailfilter's own facts ---------------------------------------------

@router.get("/server")
def server_info(_: AdminUser) -> dict:
    s = get_server()
    return {"hostname": s.hostname, "addresses": s.addresses, "spf_name": s.spf_name,
            "spf_include": s.spf_include, "domains": sorted(relay_domains())}


# Check, lookup, generators ---------------------------------------------------------

def _cloudflare_view(cf: Cloudflare, d: str, items: list[dict]) -> None:
    """Mark records that are already in Cloudflare but not yet visible in DNS.

    DNS answers are cached (a "does not exist" answer for up to 30 minutes on
    Cloudflare zones), so right after publishing the check would still say
    "missing". Cloudflare itself is the source of truth for what was published.
    """
    zone = cf.zone_for(d)
    if zone is None:
        return
    key = dkim.local_key(d)
    names = {"spf": (d, "TXT", spf.is_spf), "dmarc": (f"_dmarc.{d}", "TXT", dmarc.is_dmarc),
             "mta_sts": (f"_mta-sts.{d}", "TXT", lambda t: t.replace(" ", "").lower().startswith("v=stsv1")),
             "tls_rpt": (f"_smtp._tls.{d}", "TXT", tlssec.is_tlsrpt)}
    if key:
        names["dkim"] = (key.name, "TXT", lambda t: "p=" in t.replace(" ", ""))
    for item in items:
        if item["code"] not in names or item["status"] == "ok":
            continue
        name, rtype, match = names[item["code"]]
        recs = [r.content for r in cf.records(zone, name=name, type=rtype) if match(r.content)]
        if not recs:
            continue
        published = recs[0]
        if published == item["value"]:
            continue  # DNS already shows what Cloudflare has, so the DNS verdict stands
        fix = item.get("fix")
        if fix and (fix == published or (item["code"] == "dkim" and key
                                         and dkim.parse_record(published).get("p") == key.public_key)):
            fix = None
        item.update(status="pending", value=published, problems=["waiting_for_dns"], fix=fix)


@router.get("/check/{domain}")
def check(domain: str, _: AdminUser, db: DB) -> dict:
    d = _domain(domain)
    server = get_server()
    # The domain's own records come from its authoritative name servers, so a record
    # published a minute ago is not hidden by a cached "does not exist".
    out = domaincheck.check_domain(d, server, FreshTxt(get_resolver(), d), fetch_policy=fetch_sts_policy,
                                   tlsa_expected=local_tlsa(server.hostname)).to_dict()
    out["managed"] = d in relay_domains()
    out["golive"] = golive.state(d)
    token = get_secret(db, TOKEN_KEY)
    if token:
        try:
            _cloudflare_view(make_cloudflare(token), d, out["items"])
        except Exception:  # the DNS check itself is still useful without Cloudflare
            log.exception("reading records from Cloudflare for %s failed", d)
    return out


@router.get("/mx/{domain}")
def mx(domain: str, _: AdminUser, probe: bool = True) -> dict:
    return lookup.mx_lookup(_domain(domain), get_resolver(), probe=smtp_probe if probe else None).to_dict()


@router.get("/mx/{domain}/cloudflare")
def mx_cloudflare(domain: str, _: AdminUser, db: DB) -> dict:
    """The MX records as they are in Cloudflare (what the MX editor starts from)."""
    d = _domain(domain)
    cf = _cf(db)
    zone = _zone(cf, d)
    try:
        recs = cf.records(zone, name=d, type="MX")
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    server = get_server()
    return {"domain": d, "zone": zone.name, "managed": d in relay_domains(), "hostname": server.hostname,
            "mx_priority": server.mx_priority, "golive": golive.state(d),
            "records": sorted(({"priority": r.priority or 0, "host": norm(r.content)} for r in recs),
                              key=lambda r: (r["priority"], r["host"]))}


class SpfIn(BaseModel):
    includes: list[str] = []
    ip4: list[str] = []
    ip6: list[str] = []
    mx: bool = False
    a: bool = False
    all: Literal["~", "-", "?"] = "~"
    mailfilter: bool = True


@router.post("/spf/generate")
def spf_generate(body: SpfIn, _: AdminUser) -> dict:
    server = get_server()
    inc = ([server.spf_name] if body.mailfilter else []) + [i for i in body.includes if i.strip()]
    try:
        value = spf.generate(includes=inc, ip4=[i for i in body.ip4 if i.strip()], ip6=[i for i in body.ip6 if i.strip()],
                             mx=body.mx, a=body.a, all_qualifier=body.all)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid_address:{str(exc).split(' ')[0]}") from exc
    count = spf.count_lookups("spf-generator.invalid", get_resolver(), value)
    return {"value": value, "lookups": count.lookups, "problems": spf.problems(value) +
            [e for e in count.errors if e.startswith("too_many")]}


class DmarcIn(BaseModel):
    policy: Literal["none", "quarantine", "reject"] = "none"
    subdomain_policy: Literal["none", "quarantine", "reject"] | None = None
    pct: int = Field(default=100, ge=0, le=100)
    rua: list[str] = []
    ruf: list[str] = []
    adkim: Literal["r", "s"] = "r"
    aspf: Literal["r", "s"] = "r"


@router.post("/dmarc/generate")
def dmarc_generate(body: DmarcIn, _: AdminUser) -> dict:
    try:
        value = dmarc.generate(dmarc.DmarcOptions(
            body.policy, body.subdomain_policy, body.pct, [a for a in body.rua if a.strip()] or None,
            [a for a in body.ruf if a.strip()] or None, body.adkim, body.aspf))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_email") from exc
    return {"value": value, "status": dmarc.judge(value)[0], "problems": dmarc.judge(value)[1]}


# Publishing to Cloudflare -------------------------------------------------------------

class MxIn(BaseModel):
    priority: int = Field(ge=0, le=65535)
    host: str = Field(min_length=1, max_length=253)


class PublishIn(BaseModel):
    kind: Literal["spf", "spf_value", "dkim", "dmarc", "server_spf", "mx", "mta_sts", "tls_rpt", "tlsa", "dmarc_rua", "dmarc_policy", "dmarc_auth", "bimi"]
    domain: str | None = None
    value: str | None = Field(default=None, max_length=2000)
    mx: list[MxIn] = Field(default=[], max_length=10)


def _plan(cf: Cloudflare, body: PublishIn) -> publish.Plan:
    server = get_server()
    if body.kind == "server_spf":
        target = server.spf_name
    elif body.kind in ("tlsa", "dmarc_auth"):
        target = server.hostname  # dmarc_auth: *._report._dmarc.<hostname>, in the zone of the server's own name
    else:
        target = _domain(body.domain or "")
    zone = _zone(cf, target)
    plan = publish.Plan(zone.id, zone.name, target)
    try:
        if body.kind == "spf":
            plan.changes.append(publish.plan_spf(cf, zone, target, [server.spf_include]))
        elif body.kind == "spf_value":
            plan.changes.append(publish.plan_spf_value(cf, zone, target, body.value or ""))
        elif body.kind == "server_spf":
            if not server.addresses:
                raise HTTPException(status.HTTP_409_CONFLICT, "no_server_addresses")
            plan.changes.append(publish.plan_server_spf(cf, zone, server))
        elif body.kind == "dkim":
            key = dkim.local_key(target)
            if key is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "no_dkim_key")
            plan.changes.append(publish.plan_dkim(cf, zone, key))
        elif body.kind == "mta_sts":
            pol = tlssec.get_policy(target)
            if pol is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "no_mta_sts_policy")
            plan.changes.extend(publish.plan_mta_sts(cf, zone, target, pol.txt, server))
        elif body.kind == "tls_rpt":
            plan.changes.append(publish.plan_tls_rpt(cf, zone, target, tlssec.tlsrpt_value(_split(body.value))))
        elif body.kind == "tlsa":
            value = local_tlsa(server.hostname)
            if value is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "no_local_cert")
            plan.changes.extend(publish.plan_tlsa(cf, zone, server.hostname, value))
        elif body.kind == "dmarc_rua":
            plan.changes.append(publish.plan_dmarc_merge(cf, zone, target, add_rua=rua_address(server.hostname)))
        elif body.kind == "dmarc_policy":
            if body.value not in dmarc.POLICIES:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_policy")
            plan.changes.append(publish.plan_dmarc_merge(cf, zone, target, policy=body.value))
        elif body.kind == "dmarc_auth":
            plan.changes.append(publish.plan_report_auth(cf, zone, server.hostname))
        elif body.kind == "bimi":
            if bimi.read_file(target, "svg") is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "no_bimi_logo")
            value = bimi.record_value(server.hostname, target, bimi.read_file(target, "pem") is not None)
            plan.changes.extend(publish.plan_bimi(cf, zone, target, value))
        elif body.kind == "mx":
            if not body.mx:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_mx")
            hosts = [_mx_host(m.host) for m in body.mx]
            if len(set(hosts)) != len(hosts):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "duplicate_mx")
            plan.changes.extend(publish.plan_mx(cf, zone, target, [(m.priority, h) for m, h in zip(body.mx, hosts)]))
        else:
            plan.changes.append(publish.plan_dmarc(cf, zone, target, body.value or ""))
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    except (spf.SpfSyntaxError, dmarc.DmarcSyntaxError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_record") from exc
    except ValueError as exc:  # multiple_spf / multiple_dmarc, no_dmarc, or invalid_email:... from TLS-RPT
        code = str(exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST if code.startswith(("invalid_", "no_")) else status.HTTP_409_CONFLICT,
                            code) from exc
    return plan


def _split(v: str | None) -> list[str]:
    return [x for x in re.split(r"[\s,;]+", v or "") if x]


def _mx_host(host: str) -> str:
    h = norm(host)
    if not re.match(DOMAIN_RE, h):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid_mx_host:{host[:100]}")
    return h


def _warnings(body: PublishIn, plan: publish.Plan) -> list[str]:
    """Things to point out before an MX change is applied."""
    server = get_server()
    if body.kind == "tlsa":
        return [] if tlssec.signed(server.hostname, get_resolver()) is not False else ["dnssec_off_host"]
    if body.kind == "bimi":
        return [f"bimi_{p.split(':')[0]}" for p in bimi.dmarc_check(plan.domain, get_resolver())["problems"][:1]]
    if body.kind != "mx":
        return []
    hosts = [norm(m.host) for m in body.mx]
    out = []
    if server.hostname in hosts and plan.domain not in relay_domains():
        out.append("not_relay_domain")  # Mailfilter would refuse the mail
    if server.hostname not in hosts and (golive.state(plan.domain) or {}).get("status") == "live":
        out.append("mx_leaves_mailfilter")
    return out


@router.post("/publish/plan")
def publish_plan(body: PublishIn, _: AdminUser, db: DB) -> dict:
    plan = _plan(_cf(db), body)
    return {**plan.to_dict(), "warnings": _warnings(body, plan)}


@router.post("/publish/apply")
def publish_apply(body: PublishIn, request: Request, user: AdminUser, db: DB) -> dict:
    cf = _cf(db)
    plan = _plan(cf, body)
    try:
        done = publish.apply(cf, plan, actor=user.email)
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    batch = done[0]["batch"] if done else None
    audit(db, request, user, f"dns.publish.{body.kind}", plan.domain, batch=batch, changes=len(done))
    db.commit()
    return {"batch": batch, "applied": done, "plan": plan.to_dict()}


@router.get("/journal")
def journal(_: AdminUser, limit: int = 50) -> list[dict]:
    return list(reversed(publish.read_journal(limit=max(1, min(limit, 500)))))


@router.post("/undo/{batch}")
def undo(batch: str, request: Request, user: AdminUser, db: DB) -> dict:
    cf = _cf(db)
    try:
        done = publish.undo_batch(cf, batch, actor=user.email)
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, request, user, "dns.undo", batch, changes=len(done))
    db.commit()
    return {"undone": done}


# MTA-STS, TLS-RPT and DANE ------------------------------------------------------------

def _tlsrpt_default(d: str) -> str:
    """Where TLS reports go by default: the DMARC report address, else tlsrpt@<domain>."""
    try:
        rec, _ = dmarc.find_dmarc(get_resolver().txt(f"_dmarc.{d}"))
        rua = dmarc.parse(rec).get("rua", "") if rec else ""
    except Exception:
        rua = ""
    first = rua.split(",")[0].strip()
    return first.removeprefix("mailto:").split("!")[0] if first.startswith("mailto:") else f"tlsrpt@{d}"


@router.get("/tls/{domain}")
def tls_status(domain: str, _: AdminUser) -> dict:
    d = _domain(domain)
    server = get_server()
    resolver = get_resolver()
    try:
        current = [m.host for m in resolver.mx(d)]
    except DnsError:
        current = []
    pol = tlssec.get_policy(d)
    dane = tlssec.dane_status(d, server.hostname, resolver, expected=local_tlsa(server.hostname))
    return {
        "domain": d, "hostname": server.hostname, "managed": d in relay_domains(),
        "policy": pol.to_dict() if pol else None,
        "policy_default": {"mode": "testing", "max_age": 604800, "mx": list(dict.fromkeys([server.hostname, *current]))},
        "policy_url": f"https://mta-sts.{d}/.well-known/mta-sts.txt",
        "tlsrpt_default": _tlsrpt_default(d),
        "dane": dane.to_dict(),
    }


class StsIn(BaseModel):
    mode: Literal["testing", "enforce", "none"] = "testing"
    mx: list[str] = Field(default=[], max_length=20)
    max_age: int = Field(default=604800, ge=86400, le=31557600)


@router.put("/mta-sts/{domain}")
def mta_sts_save(domain: str, body: StsIn, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(domain)
    if d not in relay_domains():
        raise HTTPException(status.HTTP_409_CONFLICT, "not_relay_domain")
    try:
        pol = tlssec.save_policy(d, body.mode, body.mx, body.max_age)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit(db, request, user, "dns.mta_sts_policy", d, mode=pol.mode, id=pol.id)
    db.commit()
    return pol.to_dict()


@public.get("/allowed", include_in_schema=False)
def mta_sts_allowed(domain: str = "") -> Response:
    """Caddy asks this before it gets a certificate for a host name on demand."""
    host = norm(domain)
    ok = host.startswith("mta-sts.") and host[len("mta-sts."):] in relay_domains()
    return Response(status_code=200 if ok else 404)


@public.get("/policy", include_in_schema=False)
def mta_sts_policy(request: Request) -> Response:
    """Caddy rewrites ``https://mta-sts.<domain>/.well-known/mta-sts.txt`` to here."""
    pol = tlssec.policy_for_host(request.headers.get("host", ""), lambda d: d in relay_domains())
    if pol is None:
        return Response("not found\n", status_code=404, media_type="text/plain")
    return Response(pol.text(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "max-age=300"})


# Go-live -------------------------------------------------------------------------------

@router.get("/golive/{domain}")
def golive_status(domain: str, _: AdminUser, db: DB) -> dict:
    d = _domain(domain)
    try:
        return _golive_status(d, db)
    except HTTPException:
        raise
    except Exception as exc:
        # Show the real reason instead of a bare "something went wrong"
        log.exception("go-live status for %s failed", d)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"golive_error:{type(exc).__name__} {str(exc).replace(':', '')}"[:300]) from exc


def _golive_status(d: str, db) -> dict:
    server = get_server()
    pf = golive.preflight(d, server, get_resolver())
    out = {"domain": d, "state": golive.state(d), "preflight": pf.to_dict(), "plan": None, "plan_error": None,
           "hostname": server.hostname}
    if (out["state"] or {}).get("status") == "live":
        return out
    token = get_secret(db, TOKEN_KEY)
    if not token:
        out["plan_error"] = "cloudflare_not_configured"
        return out
    try:
        out["plan"] = golive.plan(make_cloudflare(token), d, server).to_dict()
    except CloudflareError as exc:
        out["plan_error"] = "cloudflare_denied" if exc.status in (401, 403) else "cloudflare_error"
    except ValueError as exc:
        out["plan_error"] = str(exc)
    return out


class GoLiveIn(BaseModel):
    confirm: str  # the domain name, typed by the admin
    force: bool = False


@router.post("/golive/{domain}")
def golive_apply(domain: str, body: GoLiveIn, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(domain)
    if norm(body.confirm) != d:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "confirm_mismatch")
    server = get_server()
    pf = golive.preflight(d, server, get_resolver())
    if not pf.ok and not body.force:
        raise HTTPException(status.HTTP_409_CONFLICT, "preflight_failed")
    cf = _cf(db)
    try:
        plan = golive.plan(cf, d, server)
        st = golive.go_live(cf, plan, actor=user.email)
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, request, user, "dns.golive", d, batch=st.get("batch"), forced=body.force and not pf.ok)
    db.commit()
    return st


@router.post("/golive/{domain}/rollback")
def golive_rollback(domain: str, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(domain)
    cf = _cf(db)
    try:
        st = golive.rollback(cf, d, actor=user.email)
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit(db, request, user, "dns.golive_rollback", d)
    db.commit()
    return st


# Blocklists ---------------------------------------------------------------------------------

@router.get("/blacklist")
def blacklist_status(user: CurrentUser) -> dict:
    data = blacklist.load()
    if not user.is_admin:
        # Customers only see whether outbound mail may be affected
        return {"checked_at": (data or {}).get("checked_at"), "listed": bool((data or {}).get("listed"))}
    return data or {"checked_at": None, "addresses": [], "listed": [], "unusable": []}


@router.post("/blacklist/check")
def blacklist_check(request: Request, user: AdminUser, db: DB) -> dict:
    ips = get_server().addresses
    if not ips:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_server_addresses")
    try:
        data = blacklist.update(ips, get_resolver())
    except (DnsError, OSError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"dns_error:{exc}") from exc
    audit(db, request, user, "dns.blacklist_check", listed=len(data["listed"]))
    db.commit()
    return data

