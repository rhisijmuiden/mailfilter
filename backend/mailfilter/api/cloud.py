"""Cloud mail: put a Microsoft 365 or Google Workspace tenant behind the Mailfilter.

A wizard per domain. Mailfilter does its own part itself (domain, delivery to
the provider, letting the provider's servers relay for this tenant only, see
mailfilter_config.cloud) and explains the provider's part: a ready PowerShell
script for Microsoft 365, click-by-click steps for Google. Every step that can
be checked from here is checked: MX/SPF/DKIM in DNS, inbound mail delivered to
the provider and outbound mail from the tenant seen in the mail trace.

Admins manage everything. Customers see the wizard of their own domains and
can tick off the steps on the provider's side.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mailfilter_config import cloud
from mailfilter_config.validate import normalise_domain
from mailfilter_dns import spf as spf_mod
from mailfilter_dns.resolver import DnsError, FreshTxt

from .. import mailer
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser, visible_domains
from ..gateway import apply_changes, ensure_imported
from ..models import CloudTenant, Domain, DomainDestination, DomainRoute, TraceMessage, TraceRecipient, User
from . import dns as dns_api

router = APIRouter(prefix="/cloud", tags=["cloud"])
log = logging.getLogger("mailfilter.cloud")

Provider = Literal["m365", "google"]

STEPS: dict[str, list[str]] = {
    "m365": ["detect", "mailfilter", "inbound_connector", "enhanced_filtering", "outbound_connector", "test", "dns",
             "lockdown"],
    "google": ["detect", "mailfilter", "dkim", "inbound_gateway", "outbound_gateway", "test", "dns", "lockdown"],
}
# Steps on the provider's side: people say when they are done.
MANUAL = {"inbound_connector", "enhanced_filtering", "outbound_connector", "dkim", "inbound_gateway",
          "outbound_gateway", "test", "lockdown"}
LOCKDOWN_WAIT = timedelta(hours=48)
# Host names the provider's inbound servers have (seen as relay host in the trace).
PROVIDER_SPF = {"m365": "include:spf.protection.outlook.com", "google": "include:_spf.google.com"}
PROVIDER_HOSTS = {"m365": ("outlook.com", "mx.microsoft"), "google": ("google.com", "googlemail.com")}


# ---- swappable in tests ----------------------------------------------------------
def fetch_tenant_id(domain: str) -> str | None:
    """The Microsoft Entra tenant ID of a domain, from Microsoft's public sign-in metadata."""
    import httpx

    try:
        r = httpx.get(f"https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration", timeout=8)
        m = re.search(r"login\.microsoftonline\.com/([0-9a-f-]{36})/", r.json().get("issuer", ""))
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001 - only a convenience: the admin can type it in
        log.info("tenant ID lookup for %s failed", domain, exc_info=True)
        return None


# ---- schemas -------------------------------------------------------------------
class TenantIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    provider: Provider
    tenant_id: str = Field(default="", max_length=36)
    # Where clean inbound mail goes; empty = the provider's default for this domain.
    destinations: list[str] = Field(default=[], max_length=10)
    # Domain already routed elsewhere: replace its destinations with the provider's.
    replace_destinations: bool = True
    customer_id: int | None = None


class TenantUpdate(BaseModel):
    tenant_id: str | None = Field(default=None, max_length=36)
    enabled: bool | None = None


class StepIn(BaseModel):
    done: bool = True


class TestIn(BaseModel):
    to: str = Field(min_length=3, max_length=320)


# ---- helpers -------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant(db, user, name: str) -> CloudTenant:
    d = normalise_domain(name)
    t = db.execute(select(CloudTenant).join(Domain).where(func.lower(Domain.name) == (d or ""))).scalar_one_or_none()
    scope = visible_domains(user)
    if t is None or (scope is not None and t.domain.name not in scope):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return t


def _commit(db, request: Request, user: User, action: str, target: str, **detail) -> None:
    apply_changes(db)
    audit(db, request, user, action, target, domain=target, **detail)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "already_exists")


def _default_destinations(provider: str, domain: str, mx_hosts: list[str]) -> list[str]:
    if provider == "google":
        return list(cloud.GOOGLE_DESTINATIONS)
    ms = [h for h in mx_hosts if cloud.detect_provider([h]) == "m365"]
    return ms[:1] or [cloud.m365_destination(domain)]


def _mx_hosts(domain: str) -> list[str]:
    try:
        return [m.host for m in sorted(dns_api.get_resolver().mx(domain), key=lambda m: m.priority)]
    except DnsError:
        return []


def _set_steps(t: CloudTenant, **changes) -> None:
    steps = dict(t.steps or {})
    for k, v in changes.items():
        if v is None:
            steps.pop(k, None)
        else:
            steps[k] = v
    t.steps = steps  # a new dict, so SQLAlchemy sees the change


def _outbound_seen(db, domain: str) -> datetime | None:
    q = (select(func.max(TraceMessage.first_seen)).where(TraceMessage.symbols.has_key("MF_CLOUD_TENANT_OK"))
         .where(TraceMessage.sender.ilike(f"%@{domain}")))
    return db.execute(q).scalar_one_or_none()


def _forged(db, domain: str) -> int:
    q = (select(func.count()).select_from(TraceMessage).where(TraceMessage.symbols.has_key("MF_CLOUD_TENANT_FORGED"))
         .where(TraceMessage.sender.ilike(f"%@{domain}")).where(TraceMessage.first_seen > _now() - timedelta(days=7)))
    return db.execute(q).scalar_one()


def _inbound_seen(db, domain: str, provider: str) -> datetime | None:
    hosts = PROVIDER_HOSTS[provider]
    q = (select(func.max(TraceMessage.first_seen)).join(TraceRecipient, TraceRecipient.queue_id == TraceMessage.queue_id)
         .where(TraceRecipient.address.ilike(f"%@{domain}")).where(TraceRecipient.status == "delivered")
         .where(func.lower(func.coalesce(TraceRecipient.relay_host, "")).op("~")("(" + "|".join(re.escape(h) for h in hosts) + ")$")))
    return db.execute(q).scalar_one_or_none()


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _summary(t: CloudTenant, states: dict[str, str]) -> dict:
    order = STEPS[t.provider]
    done = sum(1 for s in order if states.get(s) == "done")
    nxt = next((s for s in order if states.get(s) != "done"), None)
    return {"done": done, "total": len(order), "next": nxt, "at": _now().isoformat()}


def _dns_state(t: CloudTenant) -> dict:
    """MX, SPF and (Google) DKIM as they are in DNS right now."""
    d = t.domain.name
    server = dns_api.get_server()
    out: dict = {"mx": "error", "spf": "error", "mx_value": "", "spf_value": "", "spf_fix": None, "dkim": None}
    resolver = FreshTxt(dns_api.get_resolver(), d)
    try:
        mxs = sorted(resolver.mx(d), key=lambda m: m.priority)
        hosts = {m.host for m in mxs}
        out["mx_value"] = " ".join(f"{m.priority} {m.host}." for m in mxs)
        out["mx"] = "ok" if hosts == {server.hostname} else ("weak" if server.hostname in hosts else "missing")
    except Exception:  # noqa: BLE001 - the wizard still works without DNS
        log.info("MX lookup for %s failed", d, exc_info=True)
    try:
        rec, _ = spf_mod.find_spf(resolver.txt(d))
        provider_include = PROVIDER_SPF[t.provider]
        ok = bool(rec) and spf_mod.contains(rec, provider_include) and (spf_mod.contains(rec, server.spf_include) or any(
            spf_mod.contains(rec, f"ip{'6' if ':' in a else '4'}:{a}") for a in server.addresses))
        out["spf"] = "ok" if ok else "missing"
        out["spf_value"] = rec or ""
        if not ok:
            try:
                # The provider's include stays (or is added): it may still send directly, e.g. calendar mail.
                out["spf_fix"] = spf_mod.merge(rec, [provider_include, server.spf_include])
            except spf_mod.SpfSyntaxError:
                out["spf_fix"] = None
    except Exception:  # noqa: BLE001
        log.info("SPF lookup for %s failed", d, exc_info=True)
    if t.provider == "google":
        try:
            txt = resolver.txt(f"google._domainkey.{d}")
            out["dkim"] = "ok" if any("p=" in x for x in txt) else "missing"
        except Exception:  # noqa: BLE001
            out["dkim"] = "error"
    return out


def _detail(db, t: CloudTenant, *, with_dns: bool = True) -> dict:
    d = t.domain.name
    steps = t.steps or {}
    route = db.get(DomainRoute, t.domain_id)
    dests = [x.host for x in route.destinations] if route else []
    outbound = _outbound_seen(db, d)
    inbound = _inbound_seen(db, d, t.provider)
    dns_info = _dns_state(t) if with_dns else None

    def confirmed(s: str) -> bool:
        return bool(steps.get(s, {}).get("at")) if isinstance(steps.get(s), dict) else False

    st: dict[str, str] = {"detect": "done"}
    st["mailfilter"] = "done" if (route and route.kind == "transport" and route.enabled and dests and t.enabled) else "todo"
    inbound_step = "inbound_connector" if t.provider == "m365" else "inbound_gateway"
    outbound_step = "outbound_connector" if t.provider == "m365" else "outbound_gateway"
    st[inbound_step] = "done" if confirmed(inbound_step) or inbound else "todo"
    if t.provider == "m365":
        st["enhanced_filtering"] = "done" if confirmed("enhanced_filtering") else "todo"
    else:
        dk = (dns_info or {}).get("dkim")
        st["dkim"] = "done" if dk == "ok" or (confirmed("dkim") and dk != "missing") else "todo"
    st[outbound_step] = "done" if outbound else ("waiting" if confirmed(outbound_step) else "todo")
    st["test"] = "done" if confirmed("test") or (inbound and outbound) else "todo"

    mx_since = (steps.get("dns") or {}).get("mx_since") if isinstance(steps.get("dns"), dict) else None
    if dns_info is not None:
        mx_ok = dns_info["mx"] == "ok"
        if mx_ok and not mx_since:
            mx_since = _now().isoformat()
            _set_steps(t, dns={"mx_since": mx_since})
        elif not mx_ok and dns_info["mx"] != "error" and mx_since:
            mx_since = None
            _set_steps(t, dns=None)
        st["dns"] = "done" if mx_ok and dns_info["spf"] == "ok" else "todo"
    else:
        st["dns"] = "done" if mx_since else "todo"
    lockdown_ready = bool(mx_since) and datetime.fromisoformat(mx_since) <= _now() - LOCKDOWN_WAIT
    st["lockdown"] = "done" if confirmed("lockdown") else "todo"

    summary = _summary(t, st)
    if (steps.get("_summary") or {}).get("done") != summary["done"] or (steps.get("_summary") or {}).get("next") != summary["next"]:
        _set_steps(t, _summary=summary)
    server = dns_api.get_server()
    return {
        "domain": d, "provider": t.provider, "tenant_id": t.tenant_id, "enabled": t.enabled,
        "destinations": dests, "route_kind": route.kind if route else None,
        "steps": [{"id": s, "state": st[s], "manual": s in MANUAL,
                   "confirmed_at": (steps.get(s) or {}).get("at") if isinstance(steps.get(s), dict) else None,
                   "confirmed_by": (steps.get(s) or {}).get("by") if isinstance(steps.get(s), dict) else None}
                  for s in STEPS[t.provider]],
        "summary": summary,
        "evidence": {"inbound_seen": _iso(inbound), "outbound_seen": _iso(outbound), "forged_7d": _forged(db, d),
                     "mx_since": mx_since, "lockdown_ready": lockdown_ready},
        "dns": dns_info,
        "server": {"hostname": server.hostname, "v4": server.public_v4, "v6": server.public_v6,
                   "spf_include": server.spf_include},
    }


def _list_item(t: CloudTenant) -> dict:
    s = (t.steps or {}).get("_summary") or {"done": 2, "total": len(STEPS[t.provider]), "next": STEPS[t.provider][2]}
    return {"domain": t.domain.name, "provider": t.provider, "tenant_id": t.tenant_id, "enabled": t.enabled,
            "summary": s, "customers": [], "created_at": _iso(t.created_at)}


# ---- endpoints -----------------------------------------------------------------------
@router.get("")
def list_tenants(user: CurrentUser, db: DB) -> list[dict]:
    q = select(CloudTenant).join(Domain).order_by(Domain.name)
    scope = visible_domains(user)
    if scope is not None:
        q = q.where(Domain.name.in_(scope or [""]))
    out = []
    for t in db.execute(q).scalars():
        item = _list_item(t)
        if user.is_admin:
            item["customers"] = sorted(u.email for u in db.execute(
                select(User).join(User.domains).where(Domain.id == t.domain_id, User.role == "customer")).scalars())
        out.append(item)
    return out


@router.get("/detect/{domain}")
def detect(domain: str, _: AdminUser, db: DB) -> dict:
    """What the domain uses now: provider from its MX, Microsoft tenant ID, and whether
    the domain is already on the Mailfilter."""
    d = normalise_domain(domain)
    if not d:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_domain")
    mx = _mx_hosts(d)
    provider = cloud.detect_provider(mx)
    dom = db.execute(select(Domain).where(func.lower(Domain.name) == d)).scalar_one_or_none()
    route = db.get(DomainRoute, dom.id) if dom else None
    tenant_id = fetch_tenant_id(d) if provider in (None, "m365") else None
    if provider is None and tenant_id:
        provider = "m365"  # MX already elsewhere (e.g. on Mailfilter), but the domain is in Microsoft 365
    customers = [] if dom is None else sorted(u.id for u in db.execute(
        select(User).join(User.domains).where(Domain.id == dom.id, User.role == "customer")).scalars())
    return {
        "domain": d, "mx": mx, "provider": provider, "tenant_id": tenant_id,
        "destinations": _default_destinations(provider or "m365", d, mx) if provider else [],
        "exists": dom is not None, "configured": route is not None,
        "current_destinations": [x.host for x in route.destinations] if route else [],
        "linked": dom is not None and db.get(CloudTenant, dom.id) is not None,
        "customer_ids": customers,
    }


@router.post("", status_code=201)
def create_tenant(body: TenantIn, request: Request, admin: AdminUser, db: DB) -> dict:
    """Step 2: set up Mailfilter's side in one go."""
    ensure_imported(db)
    d = normalise_domain(body.domain)
    if not d:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_domain")
    dom = db.execute(select(Domain).where(func.lower(Domain.name) == d)).scalar_one_or_none()
    if dom is None:
        dom = Domain(name=d)
        db.add(dom)
        db.flush()
    elif db.get(CloudTenant, dom.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already_linked")
    dests = [h.strip().lower().rstrip(".") for h in body.destinations if h.strip()]
    dests = dests or _default_destinations(body.provider, d, _mx_hosts(d))
    route = db.get(DomainRoute, dom.id)
    if route is None:
        route = DomainRoute(domain_id=dom.id, kind="transport", tls="encrypt", verify_recipients=True)
        db.add(route)
        db.flush()
        replace = True
    else:
        replace = body.replace_destinations or route.kind != "transport" or not route.destinations
        route.kind = "transport"
    if replace:
        # Both providers take mail over TLS; recipient checks keep unknown addresses out.
        route.tls = "encrypt"
        route.destinations = [DomainDestination(domain_id=dom.id, position=i, host=h, port=25)
                              for i, h in enumerate(dests)]
    if body.customer_id:
        cust = db.get(User, body.customer_id)
        if cust is None or cust.role != "customer":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_customer")
        if dom not in cust.domains:
            cust.domains.append(dom)
    t = CloudTenant(domain_id=dom.id, provider=body.provider, tenant_id=body.tenant_id.strip().lower(), enabled=True,
                    steps={"detect": {"at": _now().isoformat(), "by": admin.email},
                           "mailfilter": {"at": _now().isoformat(), "by": admin.email}})
    db.add(t)
    _commit(db, request, admin, "cloud_tenant_linked", d, provider=body.provider, destinations=dests if replace else None)
    db.refresh(t)
    return _detail(db, t, with_dns=False)


@router.get("/{domain}")
def get_tenant(domain: str, user: CurrentUser, db: DB) -> dict:
    t = _tenant(db, user, domain)
    out = _detail(db, t)
    db.commit()  # keeps mx_since and the progress shown in the list
    return out


@router.put("/{domain}")
def update_tenant(domain: str, body: TenantUpdate, request: Request, admin: AdminUser, db: DB) -> dict:
    t = _tenant(db, admin, domain)
    if body.tenant_id is not None:
        t.tenant_id = body.tenant_id.strip().lower()
    if body.enabled is not None:
        t.enabled = body.enabled
    _commit(db, request, admin, "cloud_tenant_updated", t.domain.name, enabled=t.enabled)
    return _detail(db, t, with_dns=False)


@router.delete("/{domain}", status_code=204)
def delete_tenant(domain: str, request: Request, admin: AdminUser, db: DB) -> None:
    """Unlink: the provider's servers may no longer relay. The domain and its route stay."""
    t = _tenant(db, admin, domain)
    name = t.domain.name
    db.delete(t)
    _commit(db, request, admin, "cloud_tenant_unlinked", name)


@router.post("/{domain}/steps/{step}")
def confirm_step(domain: str, step: str, body: StepIn, request: Request, user: CurrentUser, db: DB) -> dict:
    t = _tenant(db, user, domain)
    if step not in MANUAL or step not in STEPS[t.provider]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_a_manual_step")
    _set_steps(t, **{step: {"at": _now().isoformat(), "by": user.email} if body.done else None})
    audit(db, request, user, "cloud_step_done" if body.done else "cloud_step_undone", t.domain.name,
          domain=t.domain.name, step=step)
    db.commit()
    out = _detail(db, t, with_dns=False)
    db.commit()
    return out


@router.post("/{domain}/test")
def send_test(domain: str, body: TestIn, request: Request, user: CurrentUser, db: DB) -> dict:
    """A test message to a mailbox in the tenant; it goes through the Mailfilter to the provider."""
    t = _tenant(db, user, domain)
    to = body.to.strip()
    if "@" not in to or to.rsplit("@", 1)[1].lower() != t.domain.name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "test_address_not_in_domain")
    lang_nl = (user.language or "en") == "nl"
    subject = "Mailfilter test" if not lang_nl else "Mailfilter-test"
    text = ("This test message reached your mailbox through Mailfilter. You can delete it.\n"
            "Reply to it from Outlook or Gmail to test outgoing mail as well.\n") if not lang_nl else (
           "Dit testbericht is via Mailfilter in je mailbox aangekomen. Je mag het verwijderen.\n"
           "Beantwoord het vanuit Outlook of Gmail om ook uitgaande mail te testen.\n")
    try:
        msgid = mailer.send(db, [to], subject, text)
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"code": e.code, "detail": e.detail}) from None
    audit(db, request, user, "cloud_test_sent", t.domain.name, domain=t.domain.name, to=to)
    db.commit()
    return {"message_id": msgid}


@router.get("/{domain}/script", response_class=PlainTextResponse)
def script(domain: str, user: CurrentUser, db: DB, part: Literal["setup", "lockdown", "undo"] = "setup") -> str:
    """PowerShell for Exchange Online (Microsoft 365 only)."""
    t = _tenant(db, user, domain)
    if t.provider != "m365":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "m365_only")
    return powershell(t.domain.name, dns_api.get_server(), part)


def powershell(domain: str, server, part: str) -> str:
    host = server.hostname
    v4 = ",".join(f'"{a}"' for a in server.public_v4) or '"<IPv4 of Mailfilter>"'
    head = (
        f"# Mailfilter: {domain} behind {host}\n"
        "# Run in PowerShell as an Exchange administrator. Once per computer:\n"
        "#   Install-Module ExchangeOnlineManagement -Scope CurrentUser\n"
        "Connect-ExchangeOnline\n\n"
    )
    if part == "lockdown":
        return head + (
            "# Step 8: accept mail for your domains only when it comes through Mailfilter.\n"
            "# Mail from other places is refused from now on, so only run this once the MX\n"
            f"# of every domain in this tenant points to {host}.\n"
            f'Set-InboundConnector -Identity "Mailfilter inbound" -RestrictDomainsToIPAddresses $true\n'
            "\n# Undo (accept direct mail again):\n"
            f'#   Set-InboundConnector -Identity "Mailfilter inbound" -RestrictDomainsToIPAddresses $false\n'
        )
    if part == "undo":
        return head + (
            "# Take Mailfilter out again (first point the MX back to Microsoft).\n"
            'Remove-OutboundConnector -Identity "Mailfilter outbound" -Confirm:$false\n'
            'Remove-InboundConnector -Identity "Mailfilter inbound" -Confirm:$false\n'
        )
    return head + (
        "# Step 3: accept mail that comes in through Mailfilter.\n"
        "# (Microsoft accepts only IPv4 addresses here; Mailfilter delivers to Microsoft over IPv4.)\n"
        'New-InboundConnector -Name "Mailfilter inbound" -ConnectorType Partner -SenderDomains * `\n'
        f"  -SenderIPAddresses {v4} -RequireTls $true -RestrictDomainsToIPAddresses $false `\n"
        f'  -Comment "Inbound mail is filtered by Mailfilter ({host})"\n\n'
        "# Step 4: Enhanced Filtering - Microsoft looks at the real sender's IP address\n"
        "# instead of Mailfilter's, so SPF, DMARC and its own checks keep working.\n"
        'Set-InboundConnector -Identity "Mailfilter inbound" -EFSkipLastIP $true\n\n'
        "# Step 5: send all outgoing mail through Mailfilter.\n"
        'New-OutboundConnector -Name "Mailfilter outbound" -ConnectorType Partner -RecipientDomains * `\n'
        f"  -SmartHosts {host} -UseMXRecord $false -TlsSettings DomainValidation -TlsDomain {host} `\n"
        f'  -Comment "Outgoing mail is checked and sent by Mailfilter ({host})"\n\n'
        "# Check (both should show Enabled = True):\n"
        'Get-InboundConnector "Mailfilter inbound" | Format-List Name,Enabled,SenderIPAddresses,EFSkipLastIP\n'
        'Get-OutboundConnector "Mailfilter outbound" | Format-List Name,Enabled,SmartHosts,TlsSettings\n'
    )
