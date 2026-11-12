"""DNS toolbox, Cloudflare publishing, go-live and blocklist status (admin only).

All logic lives in ``mailfilter_dns``; this module only adds login, audit
logging and the Cloudflare token from the encrypted settings store. Changes
to Cloudflare are always re-planned on the server right before they are
applied, so the browser can never send records of its own.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from mailfilter_dns import blacklist, dkim, dmarc, domaincheck, golive, lookup, publish, spf
from mailfilter_dns.cloudflare import Cloudflare, CloudflareError
from mailfilter_dns.config import ServerConfig, relay_domains
from mailfilter_dns.resolver import DnsError, DnsResolver, Resolver, norm

from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..settings_store import get_secret, set_secret

router = APIRouter(prefix="/dns", tags=["dns"])

TOKEN_KEY = "cloudflare_token"
DOMAIN_RE = r"^(?=.{1,253}$)([a-zA-Z0-9_](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}\.?$"


# Swappable in tests -----------------------------------------------------------

def get_resolver() -> Resolver:
    return DnsResolver()


def get_server() -> ServerConfig:
    return ServerConfig.load()


def make_cloudflare(token: str) -> Cloudflare:
    return Cloudflare(token)


def smtp_probe(host: str, ip: str):
    return lookup.default_probe(host, ip)


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

@router.get("/check/{domain}")
def check(domain: str, _: AdminUser) -> dict:
    d = _domain(domain)
    server = get_server()
    out = domaincheck.check_domain(d, server, get_resolver()).to_dict()
    out["managed"] = d in relay_domains()
    out["golive"] = golive.state(d)
    return out


@router.get("/mx/{domain}")
def mx(domain: str, _: AdminUser, probe: bool = True) -> dict:
    return lookup.mx_lookup(_domain(domain), get_resolver(), probe=smtp_probe if probe else None).to_dict()


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

class PublishIn(BaseModel):
    kind: Literal["spf", "spf_value", "dkim", "dmarc", "server_spf"]
    domain: str | None = None
    value: str | None = Field(default=None, max_length=2000)


def _plan(cf: Cloudflare, body: PublishIn) -> publish.Plan:
    server = get_server()
    target = server.spf_name if body.kind == "server_spf" else _domain(body.domain or "")
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
        else:
            plan.changes.append(publish.plan_dmarc(cf, zone, target, body.value or ""))
    except CloudflareError as exc:
        raise _cf_error(exc) from exc
    except (spf.SpfSyntaxError, dmarc.DmarcSyntaxError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_record") from exc
    except ValueError as exc:  # multiple_spf / multiple_dmarc
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return plan


@router.post("/publish/plan")
def publish_plan(body: PublishIn, _: AdminUser, db: DB) -> dict:
    return _plan(_cf(db), body).to_dict()


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


# Go-live -------------------------------------------------------------------------------

@router.get("/golive/{domain}")
def golive_status(domain: str, _: AdminUser, db: DB) -> dict:
    d = _domain(domain)
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

