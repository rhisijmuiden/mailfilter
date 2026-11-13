"""Backup mode: the standby server takes over when the primary is down.

Incoming mail needs nothing from us: every domain has an MX record for the
primary (priority 10) and one for the standby (20), so senders use the
standby by themselves when the primary does not answer. The standby has the
same configuration, so it filters and delivers that mail itself.

Servers that *send* through Mailfilter (web servers, scanners) use one host
name, the failover name (e.g. smtp.example.nl). The standby checks the
primary every minute (cluster-sync timer). When the primary has not answered
over HTTPS nor on port 25 for `after_minutes`, the standby points the
failover name's A/AAAA records in Cloudflare at itself (TTL 60 s), and back
at the primary once that has answered again for `back_minutes`. Every
Cloudflare change goes through the DNS toolbox journal, so it can be undone
there, and the administrators get an e-mail.
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from mailfilter_dns import publish
from mailfilter_dns.cloudflare import Cloudflare, CloudflareError

from ..models import ClusterNode, User
from ..settings_store import get_secret, get_setting, set_setting
from . import state

TTL = 60
COMMENT_ACTOR = "cluster-failover"


def smtp_alive(addresses: list[str], timeout: float = 10.0) -> bool:
    """True when one of the addresses answers on port 25 with a 220 greeting."""
    for a in addresses:
        try:
            with socket.create_connection((a, 25), timeout=timeout) as s:
                s.settimeout(timeout)
                if s.recv(64).startswith(b"220"):
                    return True
        except OSError:
            continue
    return False


# Tests replace these.
PROBE: Callable[[list[str]], bool] = smtp_alive


def make_cloudflare(token: str) -> Cloudflare:
    return Cloudflare(token)


def _split(addresses: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"A": [], "AAAA": []}
    for a in addresses:
        try:
            out["A" if ipaddress.ip_address(a).version == 4 else "AAAA"].append(a)
        except ValueError:
            continue
    return out


def plan_addresses(cf: Cloudflare, name: str, addresses: list[str]) -> publish.Plan:
    """Make `name`'s A and AAAA records exactly `addresses` (TTL 60, not proxied).
    New records are created before old ones are deleted."""
    zone = cf.zone_for(name)
    if zone is None:
        raise state.ClusterError("failover_no_zone", name)
    want = _split(addresses)
    if not want["A"] and not want["AAAA"]:
        raise state.ClusterError("failover_no_addresses", name)
    changes: list[publish.Change] = []
    for rtype, ips in want.items():
        current = cf.records(zone, name=name, type=rtype)
        keep = set()
        for r in current:
            if r.content in ips and r.content not in keep:
                keep.add(r.content)
                after = {"type": rtype, "name": name, "content": r.content, "ttl": TTL, "proxied": False}
                same = r.ttl == TTL and not r.proxied
                changes.append(publish.Change("none" if same else "update", rtype, name, r.snapshot(), after, "failover"))
            else:
                changes.append(publish.Change("delete", rtype, name, r.snapshot(), None, "failover"))
        for ip in ips:
            if ip not in keep:
                changes.append(publish.Change("create", rtype, name, None,
                                              {"type": rtype, "name": name, "content": ip, "ttl": TTL, "proxied": False},
                                              "failover"))
    changes.sort(key=lambda c: {"create": 0, "update": 1, "none": 2, "delete": 3}[c.action])
    return publish.Plan(zone.id, zone.name, name, changes)


def point_name(db: Session, addresses: list[str], actor: str) -> list[dict]:
    """Point the failover name at these addresses. Returns the journal entries."""
    settings = state.failover_settings(db)
    name = (settings.get("hostname") or "").strip().lower().rstrip(".")
    if not name:
        raise state.ClusterError("failover_no_hostname")
    token = get_secret(db, "cloudflare_token")
    if not token:
        raise state.ClusterError("failover_no_cloudflare")
    cf = make_cloudflare(token)
    try:
        return publish.apply(cf, plan_addresses(cf, name, addresses), actor=actor)
    except CloudflareError as e:
        raise state.ClusterError("failover_cloudflare", str(e)[:300]) from None


def current_state(db: Session) -> dict[str, Any]:
    return get_setting(db, state.FAILOVER_STATE_KEY) or {}


def _since(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _notify(db: Session, subject: str, text: str) -> None:
    from .. import mailer

    admins = [u.email for u in db.execute(select(User).where(User.role == "admin", User.is_active.is_(True))).scalars()]
    if not admins:
        return
    try:
        mailer.send(db, admins, subject, text)
    except Exception:  # noqa: BLE001 - an alert that cannot be sent must not stop the failover
        pass


def primary_up(p: ClusterNode) -> bool:
    """Seen over HTTPS in the last two minutes, or its mail server answers."""
    if p.last_seen_at and p.last_seen_at > state.now() - timedelta(minutes=2):
        return True
    return PROBE(list(p.addresses or []))


def check(db: Session) -> str:
    """Run by the standby after every sync. Commits. Returns what it did:
    off, not_standby, ok, waiting, took_over, active, handing_back, handed_back, error."""
    settings = state.failover_settings(db)
    if not settings.get("enabled") or not settings.get("hostname"):
        return "off"
    if state.mode(db) != state.STANDBY or state.my_role(db) != "standby":
        return "not_standby"
    p = state.primary(db)
    me = state.self_node(db)
    if p is None or me is None:
        return "not_standby"
    st = dict(current_state(db))
    now = state.now()
    up = primary_up(p)
    result: str
    try:
        if not st.get("active"):
            st.pop("up_since", None)
            if up:
                st.pop("down_since", None)
                result = "ok"
            else:
                down_since = _since(st.get("down_since")) or now
                st["down_since"] = down_since.isoformat()
                if now - down_since >= timedelta(minutes=int(settings["after_minutes"])):
                    done = point_name(db, list(me.addresses or []), COMMENT_ACTOR)
                    st = {"active": True, "since": now.isoformat(), "batch": done[0]["batch"] if done else None,
                          "primary": p.name}
                    _notify(db, f"Mailfilter: {me.name} took over from {p.name}",
                            f"{p.name} did not answer for {settings['after_minutes']} minutes. {settings['hostname']} "
                            f"now points at {me.name} ({', '.join(me.addresses or [])}).\n\n"
                            f"{p.name} reageerde {settings['after_minutes']} minuten niet. {settings['hostname']} "
                            f"wijst nu naar {me.name}.")
                    result = "took_over"
                else:
                    result = "waiting"
        else:
            if not up:
                st.pop("up_since", None)
                result = "active"
            else:
                up_since = _since(st.get("up_since")) or now
                st["up_since"] = up_since.isoformat()
                if now - up_since >= timedelta(minutes=int(settings["back_minutes"])):
                    point_name(db, list(p.addresses or []), COMMENT_ACTOR)
                    st = {"active": False, "handed_back_at": now.isoformat()}
                    _notify(db, f"Mailfilter: {p.name} is back",
                            f"{p.name} answers again. {settings['hostname']} points at {p.name} again.\n\n"
                            f"{p.name} reageert weer. {settings['hostname']} wijst weer naar {p.name}.")
                    result = "handed_back"
                else:
                    result = "handing_back"
        st.pop("error", None)
    except state.ClusterError as e:
        st["error"] = e.code + (f": {e.detail}" if e.detail else "")
        result = "error"
    st["checked_at"] = now.isoformat()
    set_setting(db, state.FAILOVER_STATE_KEY, st)
    db.commit()
    return result


def take_over_now(db: Session, actor: str) -> None:
    """By hand, on the standby: answer for the failover name now. Commits."""
    me = state.self_node(db)
    if me is None or state.my_role(db) != "standby":
        raise state.ClusterError("not_standby")
    done = point_name(db, list(me.addresses or []), actor)
    p = state.primary(db)
    set_setting(db, state.FAILOVER_STATE_KEY, {"active": True, "since": state.now().isoformat(), "manual": True,
                                               "batch": done[0]["batch"] if done else None,
                                               "primary": p.name if p else ""})
    db.commit()


def point_at_primary(db: Session, actor: str) -> None:
    """Point the failover name at the primary (first setup, or hand back by hand). Commits."""
    p = state.primary(db)
    if p is None:
        raise state.ClusterError("not_standby")
    point_name(db, list(p.addresses or []), actor)
    if state.my_role(db) == "standby":
        set_setting(db, state.FAILOVER_STATE_KEY, {"active": False, "handed_back_at": state.now().isoformat()})
    db.commit()
