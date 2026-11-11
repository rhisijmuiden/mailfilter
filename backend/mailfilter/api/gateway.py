"""Domains, relay access, extra relay ports and throttling.

Admins manage everything. Customers see their own domains (read-only) and
manage the relay IPs linked to their own domains. Every change is applied to
the mail server straight away (see mailfilter.gateway.apply_changes).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import mailfilter_config as mfc
from mailfilter_config.validate import normalise_domain

from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser, visible_domains
from ..gateway import DEFAULTS_KEY, LAST_APPLY_KEY, apply_changes, defaults, ensure_imported, problems_detail
from ..models import (AddressRoute, DestinationPolicy, Domain, DomainDestination, DomainRoute, RelayNetwork, RelayPort,
                      TlsPolicy, User)
from ..settings_store import get_setting, set_setting

router = APIRouter(prefix="/gateway", tags=["gateway"])

Limit = Field(default=None, ge=1, le=10_000_000)


# ---- schemas -------------------------------------------------------------------
class DestinationIO(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=25, ge=1, le=65535)
    mx: bool = False
    protocol: Literal["smtp", "lmtp"] = "smtp"


class DomainIn(BaseModel):
    name: str = Field(min_length=3, max_length=253)
    destinations: list[DestinationIO] = Field(min_length=1, max_length=10)
    enabled: bool = True
    tls: Literal["may", "encrypt", "verify"] = "may"
    verify_recipients: bool = True
    concurrency: int | None = Field(default=None, ge=1, le=100)
    rate_delay: int | None = Field(default=None, ge=0, le=3600)
    out_per_hour: int | None = Limit
    out_per_day: int | None = Limit
    note: str = Field(default="", max_length=2000)


class DomainOut(DomainIn):
    id: int
    configured: bool  # False: the domain exists (e.g. for users) but mail is not routed yet
    users: list[str] = []
    relay_networks: int = 0


class NetworkIn(BaseModel):
    cidr: str = Field(min_length=2, max_length=50)
    description: str = Field(default="", max_length=200)
    domain: str | None = None
    enabled: bool = True
    allow_broad: bool = False


class NetworkOut(NetworkIn):
    id: int


class PortIn(BaseModel):
    port: int = Field(ge=1, le=65535)
    tls: Literal["may", "encrypt", "wrapper"] = "may"
    description: str = Field(default="", max_length=200)


class PortOut(PortIn):
    id: int


class PolicyIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    concurrency: int | None = Field(default=None, ge=1, le=100)
    rate_delay: int | None = Field(default=None, ge=0, le=3600)
    note: str = Field(default="", max_length=2000)


class PolicyOut(PolicyIn):
    id: int


class DefaultsIO(BaseModel):
    relay_ip_per_minute: int = Field(ge=1, le=10_000_000)
    relay_ip_per_hour: int = Field(ge=1, le=10_000_000)
    domain_per_hour: int = Field(ge=1, le=10_000_000)
    domain_per_day: int = Field(ge=1, le=10_000_000)
    user_per_hour: int = Field(ge=1, le=10_000_000)
    concurrency: int = Field(ge=1, le=100)


class ThrottlingIO(BaseModel):
    defaults: DefaultsIO
    policies: list[PolicyOut | PolicyIn] = []


class RouteIn(DestinationIO):
    address: str = Field(min_length=3, max_length=320)
    note: str = Field(default="", max_length=2000)


class RouteOut(RouteIn):
    id: int


class TlsIn(BaseModel):
    destination: str = Field(min_length=3, max_length=260)
    level: Literal["none", "may", "encrypt", "dane", "verify", "secure"] = "encrypt"
    note: str = Field(default="", max_length=2000)


class TlsOut(TlsIn):
    id: int


# ---- helpers -------------------------------------------------------------------
def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


def _commit(db, request: Request, user: User, action: str, target: str, **detail) -> None:
    apply_changes(db)
    audit(db, request, user, action, target, **detail)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "already_exists")


def _domain_out(db, dom: Domain, route: DomainRoute | None) -> DomainOut:
    users = list(db.execute(select(User.email).join(User.domains).where(Domain.id == dom.id).order_by(User.email)).scalars())
    nets = db.execute(select(func.count()).select_from(RelayNetwork).where(RelayNetwork.domain_id == dom.id)).scalar_one()
    if route is None:
        return DomainOut(id=dom.id, name=dom.name, configured=False, destinations=[DestinationIO(host="-")],
                         users=users, relay_networks=nets)
    return DomainOut(
        id=dom.id, name=dom.name, configured=True, users=users, relay_networks=nets,
        destinations=[DestinationIO(host=d.host, port=d.port, mx=d.mx, protocol=d.protocol) for d in route.destinations],
        enabled=route.enabled, tls=route.tls, verify_recipients=route.verify_recipients, concurrency=route.concurrency,
        rate_delay=route.rate_delay, out_per_hour=route.out_per_hour, out_per_day=route.out_per_day, note=route.note,
    )


def _set_route(route: DomainRoute, body: DomainIn) -> None:
    for f in ("enabled", "tls", "verify_recipients", "concurrency", "rate_delay", "out_per_hour", "out_per_day", "note"):
        setattr(route, f, getattr(body, f))
    route.destinations = [DomainDestination(domain_id=route.domain_id, position=i, host=d.host.strip(), port=d.port,
                                            mx=d.mx, protocol=d.protocol) for i, d in enumerate(body.destinations)]


def _scoped_domain(db, user: User, name: str | None) -> Domain | None:
    """The domain a relay network belongs to; customers must pick one of their own."""
    if not name:
        if not user.is_admin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "domain_required")
        return None
    dom = db.execute(select(Domain).where(func.lower(Domain.name) == name.strip().lower())).scalar_one_or_none()
    if dom is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown_domain:{name}")
    scope = visible_domains(user)
    if scope is not None and dom.name not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_your_domain")
    return dom


# ---- status ----------------------------------------------------------------------
@router.get("/status")
def gateway_status(_: AdminUser, db: DB) -> dict:
    ensure_imported(db)
    return {"last_apply": get_setting(db, LAST_APPLY_KEY), "imported": get_setting(db, "gateway.imported")}


@router.post("/apply")
def apply_now(request: Request, admin: AdminUser, db: DB) -> dict:
    """Write the configuration again, e.g. after re-running the installer."""
    ensure_imported(db)
    result = apply_changes(db)
    audit(db, request, admin, "gateway_applied", "")
    db.commit()
    return result


# ---- domains ---------------------------------------------------------------------
@router.get("/domains", response_model=list[DomainOut])
def list_domains(user: CurrentUser, db: DB) -> list[DomainOut]:
    ensure_imported(db)
    q = select(Domain, DomainRoute).outerjoin(DomainRoute, DomainRoute.domain_id == Domain.id).order_by(Domain.name)
    scope = visible_domains(user)
    if scope is not None:
        q = q.where(Domain.name.in_(scope or [""]))
    return [_domain_out(db, d, r) for d, r in db.execute(q).all()]


@router.post("/domains", response_model=DomainOut, status_code=201)
def create_domain(body: DomainIn, request: Request, admin: AdminUser, db: DB) -> DomainOut:
    ensure_imported(db)
    name = normalise_domain(body.name)
    if not name:
        raise HTTPException(422, problems_detail([mfc.Problem(
            "name", "domain_invalid", f"'{body.name}' is not a valid domain name.",
            f"'{body.name}' is geen geldige domeinnaam.")]))
    dom = db.execute(select(Domain).where(func.lower(Domain.name) == name)).scalar_one_or_none()
    if dom is None:
        dom = Domain(name=name)
        db.add(dom)
        db.flush()
    elif db.get(DomainRoute, dom.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "domain_exists")
    route = DomainRoute(domain_id=dom.id)
    _set_route(route, body)
    db.add(route)
    _commit(db, request, admin, "domain_created", name, destinations=[d.host for d in body.destinations])
    return _domain_out(db, dom, route)


@router.put("/domains/{domain_id}", response_model=DomainOut)
def update_domain(domain_id: int, body: DomainIn, request: Request, admin: AdminUser, db: DB) -> DomainOut:
    dom = db.get(Domain, domain_id)
    if dom is None:
        raise _not_found()
    route = db.get(DomainRoute, domain_id)
    if route is None:
        route = DomainRoute(domain_id=domain_id)
        db.add(route)
    _set_route(route, body)
    _commit(db, request, admin, "domain_updated", dom.name, destinations=[d.host for d in body.destinations],
            enabled=body.enabled)
    return _domain_out(db, dom, route)


@router.delete("/domains/{domain_id}", status_code=204)
def delete_domain(domain_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    dom = db.get(Domain, domain_id)
    if dom is None:
        raise _not_found()
    name = dom.name
    db.delete(dom)
    _commit(db, request, admin, "domain_deleted", name)


# ---- relay networks ----------------------------------------------------------------
def _net_out(n: RelayNetwork) -> NetworkOut:
    return NetworkOut(id=n.id, cidr=n.cidr, description=n.description, domain=n.domain.name if n.domain else None,
                      enabled=n.enabled, allow_broad=n.allow_broad)


def _visible_network(db, user: User, net_id: int) -> RelayNetwork:
    n = db.get(RelayNetwork, net_id)
    scope = visible_domains(user)
    if n is None or (scope is not None and (n.domain is None or n.domain.name not in scope)):
        raise _not_found()
    return n


def _normalise_cidr(body: NetworkIn, user: User) -> str:
    probe = mfc.GatewayConfig(relay_networks=[mfc.RelayNetwork(cidr=body.cidr, allow_broad=body.allow_broad and user.is_admin)])
    problems = mfc.validate(probe)
    if problems:
        raise HTTPException(422, problems_detail(problems))
    return probe.relay_networks[0].cidr


@router.get("/relay-networks", response_model=list[NetworkOut])
def list_networks(user: CurrentUser, db: DB) -> list[NetworkOut]:
    ensure_imported(db)
    q = select(RelayNetwork).order_by(RelayNetwork.id)
    scope = visible_domains(user)
    if scope is not None:
        q = q.join(Domain, RelayNetwork.domain_id == Domain.id).where(Domain.name.in_(scope or [""]))
    return [_net_out(n) for n in db.execute(q).scalars()]


@router.post("/relay-networks", response_model=NetworkOut, status_code=201)
def create_network(body: NetworkIn, request: Request, user: CurrentUser, db: DB) -> NetworkOut:
    ensure_imported(db)
    dom = _scoped_domain(db, user, body.domain)
    cidr = _normalise_cidr(body, user)
    if db.execute(select(RelayNetwork).where(RelayNetwork.cidr == cidr)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "network_exists")
    n = RelayNetwork(cidr=cidr, description=body.description.strip(), domain_id=dom.id if dom else None,
                     enabled=body.enabled, allow_broad=body.allow_broad and user.is_admin)
    db.add(n)
    _commit(db, request, user, "relay_network_added", cidr, domain=dom.name if dom else None)
    db.refresh(n)
    return _net_out(n)


@router.put("/relay-networks/{net_id}", response_model=NetworkOut)
def update_network(net_id: int, body: NetworkIn, request: Request, user: CurrentUser, db: DB) -> NetworkOut:
    n = _visible_network(db, user, net_id)
    dom = _scoped_domain(db, user, body.domain)
    n.cidr = _normalise_cidr(body, user)
    n.description = body.description.strip()
    n.domain_id = dom.id if dom else None
    n.enabled = body.enabled
    n.allow_broad = body.allow_broad and user.is_admin
    _commit(db, request, user, "relay_network_updated", n.cidr, enabled=n.enabled)
    db.refresh(n)
    return _net_out(n)


@router.delete("/relay-networks/{net_id}", status_code=204)
def delete_network(net_id: int, request: Request, user: CurrentUser, db: DB) -> None:
    n = _visible_network(db, user, net_id)
    cidr = n.cidr
    db.delete(n)
    _commit(db, request, user, "relay_network_deleted", cidr)


# ---- extra relay ports -------------------------------------------------------------
@router.get("/relay-ports", response_model=list[PortOut])
def list_ports(_: AdminUser, db: DB) -> list[PortOut]:
    ensure_imported(db)
    return [PortOut(id=p.id, port=p.port, tls=p.tls, description=p.description)
            for p in db.execute(select(RelayPort).order_by(RelayPort.port)).scalars()]


@router.post("/relay-ports", response_model=PortOut, status_code=201)
def create_port(body: PortIn, request: Request, admin: AdminUser, db: DB) -> PortOut:
    ensure_imported(db)
    if db.execute(select(RelayPort).where(RelayPort.port == body.port)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "port_exists")
    p = RelayPort(port=body.port, tls=body.tls, description=body.description.strip())
    db.add(p)
    _commit(db, request, admin, "relay_port_added", str(body.port), tls=body.tls)
    return PortOut(id=p.id, port=p.port, tls=p.tls, description=p.description)


@router.put("/relay-ports/{port_id}", response_model=PortOut)
def update_port(port_id: int, body: PortIn, request: Request, admin: AdminUser, db: DB) -> PortOut:
    p = db.get(RelayPort, port_id)
    if p is None:
        raise _not_found()
    p.port, p.tls, p.description = body.port, body.tls, body.description.strip()
    _commit(db, request, admin, "relay_port_updated", str(body.port), tls=body.tls)
    return PortOut(id=p.id, port=p.port, tls=p.tls, description=p.description)


@router.delete("/relay-ports/{port_id}", status_code=204)
def delete_port(port_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    p = db.get(RelayPort, port_id)
    if p is None:
        raise _not_found()
    port = p.port
    db.delete(p)
    _commit(db, request, admin, "relay_port_deleted", str(port))


# ---- throttling ----------------------------------------------------------------------
def _throttling(db) -> ThrottlingIO:
    return ThrottlingIO(
        defaults=DefaultsIO(**defaults(db).__dict__),
        policies=[PolicyOut(id=p.id, domain=p.domain, concurrency=p.concurrency, rate_delay=p.rate_delay, note=p.note)
                  for p in db.execute(select(DestinationPolicy).order_by(DestinationPolicy.domain)).scalars()],
    )


@router.get("/throttling", response_model=ThrottlingIO)
def get_throttling(_: AdminUser, db: DB) -> ThrottlingIO:
    ensure_imported(db)
    return _throttling(db)


@router.put("/throttling", response_model=ThrottlingIO)
def put_throttling(body: ThrottlingIO, request: Request, admin: AdminUser, db: DB) -> ThrottlingIO:
    """Replace the defaults and the list of throttled destinations in one go."""
    ensure_imported(db)
    set_setting(db, DEFAULTS_KEY, body.defaults.model_dump())
    existing = {p.id: p for p in db.execute(select(DestinationPolicy)).scalars()}
    keep = set()
    for item in body.policies:
        pid = getattr(item, "id", None)
        row = existing.get(pid) if pid else None
        if row is None:
            row = DestinationPolicy(domain=item.domain)
            db.add(row)
        row.domain = (normalise_domain(item.domain) or item.domain).lower()
        row.concurrency, row.rate_delay, row.note = item.concurrency, item.rate_delay, item.note
        if pid:
            keep.add(pid)
    for pid, row in existing.items():
        if pid not in keep:
            db.delete(row)
    _commit(db, request, admin, "throttling_updated", "", policies=[p.domain for p in body.policies])
    return _throttling(db)


# ---- address routes and TLS policies (generic list endpoints) -------------------------
def _crud(path: str, model, schema_in, schema_out, key: str, normalise=lambda v: v.strip().lower()):
    def to_out(row):
        return schema_out(id=row.id, **{f: getattr(row, f) for f in schema_in.model_fields})

    @router.get(path, response_model=list[schema_out], name=f"list_{model.__tablename__}")
    def list_rows(_: AdminUser, db: DB):
        ensure_imported(db)
        return [to_out(r) for r in db.execute(select(model).order_by(getattr(model, key))).scalars()]

    def create_row(body, request: Request, admin: AdminUser, db: DB):
        ensure_imported(db)
        data = body.model_dump()
        data[key] = normalise(data[key])
        if db.execute(select(model).where(getattr(model, key) == data[key])).scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, "already_exists")
        row = model(**data)
        db.add(row)
        _commit(db, request, admin, f"{model.__tablename__}_added", data[key])
        return to_out(row)

    def update_row(row_id: int, body, request: Request, admin: AdminUser, db: DB):
        row = db.get(model, row_id)
        if row is None:
            raise _not_found()
        for f, v in body.model_dump().items():
            setattr(row, f, normalise(v) if f == key else v)
        _commit(db, request, admin, f"{model.__tablename__}_updated", getattr(row, key))
        return to_out(row)

    # The body type is only known here; set it before FastAPI reads the signature
    # (string annotations from `from __future__ import annotations` cannot see it).
    for fn in (create_row, update_row):
        fn.__annotations__ = {**fn.__annotations__, "body": schema_in}
    router.post(path, response_model=schema_out, status_code=201, name=f"create_{model.__tablename__}")(create_row)
    router.put(path + "/{row_id}", response_model=schema_out, name=f"update_{model.__tablename__}")(update_row)

    @router.delete(path + "/{row_id}", status_code=204, name=f"delete_{model.__tablename__}")
    def delete_row(row_id: int, request: Request, admin: AdminUser, db: DB):
        row = db.get(model, row_id)
        if row is None:
            raise _not_found()
        target = getattr(row, key)
        db.delete(row)
        _commit(db, request, admin, f"{model.__tablename__}_deleted", target)


_crud("/address-routes", AddressRoute, RouteIn, RouteOut, "address")
_crud("/tls-policies", TlsPolicy, TlsIn, TlsOut, "destination")
