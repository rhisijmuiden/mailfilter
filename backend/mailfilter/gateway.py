"""Bridge between the database and the mail server configuration.

Every change in Domains, Relay access or Throttling goes through
apply_changes(): the database rows are turned into a mailfilter_config
GatewayConfig, validated here (so the user gets a clear message), and then
applied as root by the sudo helper, which validates again, writes the
Postfix/Rspamd/nftables files, checks them and rolls back on failure. The
database transaction is only committed when the server accepted the change.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

import mailfilter_config as mfc
from mailfilter_config.rules import rule_from_dict

from .models import (AddressRoute, DestinationPolicy, Domain, DomainDestination, DomainRoute, OutboundHold,
                     RelayNetwork, RelayPort, TlsPolicy)
from .settings import ETC, get_settings
from .settings_store import get_setting, set_setting

DEFAULTS_KEY = "gateway.defaults"
LAST_APPLY_KEY = "gateway.last_apply"
IMPORTED_KEY = "gateway.imported"
RULES_KEY = "custom_rules"


def custom_rules(db: Session) -> dict[str, mfc.CustomRule]:
    """What the admin chose on the Custom rules page (rules not in here use their default)."""
    stored = get_setting(db, RULES_KEY) or {}
    return {k: rule_from_dict(v) for k, v in stored.items()}


def defaults(db: Session) -> mfc.Defaults:
    stored = get_setting(db, DEFAULTS_KEY) or {}
    known = mfc.Defaults().__dict__.keys()
    return mfc.Defaults(**{k: v for k, v in stored.items() if k in known})


def _dest(d) -> mfc.Destination:
    return mfc.Destination(host=d.host, port=d.port, mx=d.mx, protocol=d.protocol)


def build_config(db: Session) -> mfc.GatewayConfig:
    s = get_settings()
    routes = db.execute(select(DomainRoute).join(Domain).order_by(Domain.name)).scalars().all()
    return mfc.GatewayConfig(
        hostname=s.hostname if s.hostname != "localhost" else "",
        own_addresses=[a for a in (s.public_v4, s.public_v6) if a],
        domains=[
            mfc.Domain(
                name=r.domain.name, destinations=[_dest(d) for d in r.destinations], enabled=r.enabled,
                tls=r.tls, verify_recipients=r.verify_recipients, note=r.note,
                queue=mfc.QueuePolicy(concurrency=r.concurrency, rate_delay=r.rate_delay),
                outbound=mfc.OutboundLimits(per_hour=r.out_per_hour, per_day=r.out_per_day),
            )
            for r in routes
        ],
        relay_networks=[
            mfc.RelayNetwork(cidr=n.cidr, description=n.description, customer=n.domain.name if n.domain else None,
                             enabled=n.enabled, allow_broad=n.allow_broad)
            for n in db.execute(select(RelayNetwork).order_by(RelayNetwork.id)).scalars()
        ],
        relay_ports=[mfc.RelayPort(port=p.port, tls=p.tls, description=p.description)
                     for p in db.execute(select(RelayPort).order_by(RelayPort.port)).scalars()],
        destination_policies=[
            mfc.DestinationPolicy(domain=p.domain, note=p.note,
                                  queue=mfc.QueuePolicy(concurrency=p.concurrency, rate_delay=p.rate_delay))
            for p in db.execute(select(DestinationPolicy).order_by(DestinationPolicy.domain)).scalars()
        ],
        address_routes=[mfc.AddressRoute(address=a.address, destination=_dest(a), note=a.note)
                        for a in db.execute(select(AddressRoute).order_by(AddressRoute.address)).scalars()],
        tls_policies=[mfc.TlsPolicy(destination=t.destination, level=t.level, note=t.note)
                      for t in db.execute(select(TlsPolicy).order_by(TlsPolicy.destination)).scalars()],
        holds=[mfc.OutboundHold(kind=h.kind, value=h.value, reason=hold_reason_text(h))
               for h in db.execute(select(OutboundHold).where(OutboundHold.released_at.is_(None))
                                   .order_by(OutboundHold.id)).scalars()],
        defaults=defaults(db),
        custom_rules=custom_rules(db),
    )


def hold_reason_text(h) -> str:
    """Short English reason Postfix logs with every held message."""
    d = h.detail or {}
    if h.reason == "spam":
        return f"{d.get('spam', '?')} of {d.get('total', '?')} messages were spam in {d.get('minutes', '?')} minutes"
    if h.reason == "volume":
        return f"{d.get('total', '?')} messages in {d.get('minutes', '?')} minutes, usually {d.get('usual', 0)}"
    return "paused by an administrator"


def problems_detail(problems: list[mfc.Problem]) -> dict[str, Any]:
    return {"code": "invalid_config", "problems": [p.__dict__ for p in problems]}


def run_helper(config_json: str) -> dict[str, Any]:
    """Runs `mailfilter-config apply --stdin --json` as root. Tests replace this."""
    cmd = [*get_settings().config_helper, "apply", "--stdin", "--json"]
    try:
        res = subprocess.run(cmd, input=config_json, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "step": "helper", "output": str(e)}
    try:
        return json.loads(res.stdout)
    except ValueError:
        return {"ok": False, "step": "helper", "output": (res.stdout + res.stderr).strip()[-4000:]}


def apply_changes(db: Session) -> dict[str, Any]:
    """Validate and apply the pending (flushed, not committed) changes.

    Raises HTTPException and rolls the transaction back when the change is
    refused; the caller commits on success."""
    db.flush()
    cfg = build_config(db)
    problems = mfc.validate(cfg)
    if problems:
        db.rollback()
        raise HTTPException(422, problems_detail(problems))
    result = run_helper(cfg.to_json())
    record = {"at": datetime.now(timezone.utc).isoformat(), **{k: v for k, v in result.items() if k != "problems"}}
    if not result.get("ok"):
        db.rollback()
        if result.get("problems"):
            raise HTTPException(422,
                                {"code": "invalid_config", "problems": result["problems"]})
        set_setting(db, LAST_APPLY_KEY, record)
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            {"code": "apply_failed", "step": result.get("step", ""), "output": result.get("output", "")})
    set_setting(db, LAST_APPLY_KEY, record)
    return result


# ---- first start: take over what the installer wrote ---------------------------
def ensure_imported(db: Session, state_file: Path | None = None) -> None:
    """Once: copy /etc/mailfilter/gateway.json (written by the installer) into the database."""
    if get_setting(db, IMPORTED_KEY):
        return
    path = state_file or ETC / "gateway.json"
    try:
        cfg = mfc.GatewayConfig.from_json(path.read_text())
    except (OSError, ValueError, TypeError, KeyError):
        cfg = mfc.GatewayConfig()
    mfc.validate(cfg)  # normalises names; problems are shown on the next save
    by_name = {d.name: d for d in db.execute(select(Domain)).scalars()}
    for d in cfg.domains:
        dom = by_name.get(d.name) or Domain(name=d.name)
        db.add(dom)
        db.flush()
        by_name[d.name] = dom
        if db.get(DomainRoute, dom.id) is None:
            route = DomainRoute(domain_id=dom.id, enabled=d.enabled, tls=d.tls, verify_recipients=d.verify_recipients,
                                concurrency=d.queue.concurrency, rate_delay=d.queue.rate_delay,
                                out_per_hour=d.outbound.per_hour, out_per_day=d.outbound.per_day, note=d.note)
            route.destinations = [DomainDestination(domain_id=dom.id, position=i, host=x.host, port=x.port, mx=x.mx,
                                                    protocol=x.protocol) for i, x in enumerate(d.destinations)]
            db.add(route)
    have = set(db.execute(select(RelayNetwork.cidr)).scalars())
    for n in cfg.relay_networks:
        if n.cidr not in have:
            owner = by_name.get(n.customer or "")
            db.add(RelayNetwork(cidr=n.cidr, description=n.description, enabled=n.enabled,
                                allow_broad=n.allow_broad, domain_id=owner.id if owner else None))
            have.add(n.cidr)
    ports = set(db.execute(select(RelayPort.port)).scalars())
    for p in cfg.relay_ports:
        if p.port not in ports:
            db.add(RelayPort(port=p.port, tls=p.tls, description=p.description))
            ports.add(p.port)
    for p in cfg.destination_policies:
        db.add(DestinationPolicy(domain=p.domain, concurrency=p.queue.concurrency, rate_delay=p.queue.rate_delay,
                                 note=p.note))
    for a in cfg.address_routes:
        x = a.destination
        db.add(AddressRoute(address=a.address, host=x.host, port=x.port, mx=x.mx, protocol=x.protocol, note=a.note))
    for t in cfg.tls_policies:
        db.add(TlsPolicy(destination=t.destination, level=t.level, note=t.note))
    if cfg.defaults != mfc.Defaults():
        set_setting(db, DEFAULTS_KEY, cfg.defaults.__dict__)
    if cfg.custom_rules and get_setting(db, RULES_KEY) is None:
        set_setting(db, RULES_KEY, {k: v.__dict__ for k, v in cfg.custom_rules.items()})
    set_setting(db, IMPORTED_KEY, {"at": datetime.now(timezone.utc).isoformat(), "from": str(path)})
    db.commit()
