"""Checks a GatewayConfig before anything touches the server.

Every problem has a stable code (for the web app), the field it belongs to,
and a message in English and Dutch. Validation also normalises values in
place (lower-case domains, canonical networks), so render() gets clean input.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from .model import ALL_TLS_MODES, LISTENER_TLS, PROTOCOLS, TLS_MODES, GatewayConfig

# Ports the gateway itself uses; an extra relay listener may not take them.
RESERVED_PORTS = {22, 25, 53, 80, 443, 465, 587, 3310, 5432, 6379, 11332, 11333, 11334, 11335}
# Where the gateway accepts mail itself: delivering to these on a local address is a loop.
OWN_SMTP_PORTS = {25, 465, 587}
MIN_PREFIX = {4: 16, 6: 32}

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


@dataclass
class Problem:
    field: str
    code: str
    en: str
    nl: str

    def __str__(self) -> str:
        return f"{self.field}: {self.en}"


class ConfigError(ValueError):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems))


def normalise_domain(name: str) -> str | None:
    """Lower-case, IDNA-encoded domain name, or None when it is not valid."""
    name = (name or "").strip().rstrip(".").lower()
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = name.split(".")
    if len(name) > 253 or len(labels) < 2 or not all(_LABEL.match(lbl) for lbl in labels):
        return None
    if labels[-1].isdigit():
        return None
    return name


def _ip(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def validate(cfg: GatewayConfig) -> list[Problem]:
    problems: list[Problem] = []

    def add(fld: str, code: str, en: str, nl: str) -> None:
        problems.append(Problem(fld, code, en, nl))

    hostname = (cfg.hostname or "").lower().rstrip(".")
    own_ips = {a for a in (_ip(x) for x in cfg.own_addresses) if a}

    def is_own(host: str, port: int) -> bool:
        if hostname and host.lower().rstrip(".") == hostname:
            return True
        ip = _ip(host)
        if ip is None:
            return False
        return (ip in own_ips or ip.is_loopback or ip.is_unspecified) and port in OWN_SMTP_PORTS

    def check_destination(df: str, dest, name: str) -> bool:
        dest.host = dest.host.strip().strip("[]").rstrip(".").lower()
        if not (_ip(dest.host) or normalise_domain(dest.host)):
            add(f"{df}.host", "destination_invalid", f"'{dest.host}' is not a host name or IP address.",
                f"'{dest.host}' is geen hostnaam of IP-adres.")
            return False
        if not 1 <= int(dest.port) <= 65535:
            add(f"{df}.port", "port_invalid", "Port must be between 1 and 65535.",
                "Poort moet tussen 1 en 65535 liggen.")
        if dest.protocol not in PROTOCOLS:
            add(f"{df}.protocol", "protocol_invalid", "Protocol must be smtp or lmtp.",
                "Protocol moet smtp of lmtp zijn.")
        if is_own(dest.host, int(dest.port)):
            add(f"{df}.host", "loop_self",
                f"{dest.host} is this Mailfilter itself: mail for {name} would loop.",
                f"{dest.host} is deze Mailfilter zelf: mail voor {name} zou rondjes blijven draaien.")
        return True

    # ---- domains -------------------------------------------------------------
    all_names = {n for n in (normalise_domain(d.name) for d in cfg.domains) if n}
    seen: set[str] = set()
    for i, d in enumerate(cfg.domains):
        f = f"domains[{i}]"
        name = normalise_domain(d.name)
        if not name:
            add(f"{f}.name", "domain_invalid", f"'{d.name}' is not a valid domain name.",
                f"'{d.name}' is geen geldige domeinnaam.")
            continue
        d.name = name
        if name in seen:
            add(f"{f}.name", "domain_duplicate", f"{name} is listed twice.", f"{name} staat er twee keer in.")
        seen.add(name)
        if d.tls not in TLS_MODES:
            add(f"{f}.tls", "tls_invalid", f"TLS must be one of {', '.join(TLS_MODES)}.",
                f"TLS moet een van {', '.join(TLS_MODES)} zijn.")
        if not d.destinations:
            add(f"{f}.destinations", "destination_missing",
                f"{name} needs a mail server to deliver to.",
                f"{name} heeft een mailserver nodig om op af te leveren.")
        if len({x.protocol for x in d.destinations}) > 1:
            add(f"{f}.destinations", "protocol_mixed", "All mail servers of a domain must use the same protocol.",
                "Alle mailservers van een domein moeten hetzelfde protocol gebruiken.")
        for j, dest in enumerate(d.destinations):
            df = f"{f}.destinations[{j}]"
            if not check_destination(df, dest, name):
                continue
            if dest.mx and dest.host == name:
                add(f"{df}.mx", "loop_mx",
                    f"Looking up the MX of {name} finds this Mailfilter after go-live, so mail would loop. "
                    "Enter the real mail server instead.",
                    f"De MX van {name} wijst na livegang naar deze Mailfilter, dus mail zou rondjes draaien. "
                    "Vul de echte mailserver in.")
            if dest.host in all_names and dest.host != name and dest.mx:
                add(f"{df}.host", "loop_relay_domain",
                    f"{dest.host} is itself a domain on this Mailfilter; delivering there may loop.",
                    f"{dest.host} is zelf een domein op deze Mailfilter; afleveren daar kan een lus geven.")
        _check_queue(add, f"{f}.queue", d.queue)
        for attr in ("per_hour", "per_day"):
            v = getattr(d.outbound, attr)
            if v is not None and not 1 <= int(v) <= 10_000_000:
                add(f"{f}.outbound.{attr}", "limit_invalid", "Limit must be a positive number.",
                    "Limiet moet een positief getal zijn.")

    # ---- routes for single addresses -----------------------------------------
    for i, r in enumerate(cfg.address_routes):
        f = f"address_routes[{i}]"
        local, at, dom = (r.address or "").strip().lower().rpartition("@")
        ndom = normalise_domain(dom)
        if not ndom or (at and not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)):
            add(f"{f}.address", "address_invalid", f"'{r.address}' is not an email address or domain.",
                f"'{r.address}' is geen e-mailadres of domein.")
            continue
        r.address = f"{local}@{ndom}" if at else ndom
        if not at and ndom in all_names:
            add(f"{f}.address", "route_is_relay_domain",
                f"{ndom} is a domain on this Mailfilter; change its mail server on the domain itself.",
                f"{ndom} is een domein op deze Mailfilter; wijzig de mailserver bij het domein zelf.")
        check_destination(f"{f}.destination", r.destination, r.address)

    for i, t in enumerate(cfg.tls_policies):
        f = f"tls_policies[{i}]"
        t.destination = (t.destination or "").strip().lower()
        m = re.fullmatch(r"\[([^\]]+)\](?::(\d{1,5}))?|(\.?)([^\s\[\]:]+)", t.destination)
        host = m and (m.group(1) or m.group(4))
        if not m or not (_ip(host) or normalise_domain(host)):
            add(f"{f}.destination", "destination_invalid", f"'{t.destination}' is not a domain or [host]:port.",
                f"'{t.destination}' is geen domein of [host]:poort.")
        if t.level not in ALL_TLS_MODES:
            add(f"{f}.level", "tls_invalid", f"TLS must be one of {', '.join(ALL_TLS_MODES)}.",
                f"TLS moet een van {', '.join(ALL_TLS_MODES)} zijn.")

    # ---- relay networks ------------------------------------------------------
    nets_seen = set()
    for i, n in enumerate(cfg.relay_networks):
        f = f"relay_networks[{i}]"
        try:
            net = ipaddress.ip_network(n.cidr.strip(), strict=False)
        except ValueError:
            add(f"{f}.cidr", "network_invalid", f"'{n.cidr}' is not an IP address or network.",
                f"'{n.cidr}' is geen IP-adres of netwerk.")
            continue
        n.cidr = str(net.network_address) if net.prefixlen == net.max_prefixlen else str(net)
        if n.cidr in nets_seen:
            add(f"{f}.cidr", "network_duplicate", f"{n.cidr} is listed twice.", f"{n.cidr} staat er twee keer in.")
        nets_seen.add(n.cidr)
        if net.prefixlen == 0:
            add(f"{f}.cidr", "open_relay",
                "This would let the whole internet send mail through the Mailfilter (open relay).",
                "Hiermee kan het hele internet mail via de Mailfilter versturen (open relay).")
        elif net.prefixlen < MIN_PREFIX[net.version] and not n.allow_broad:
            add(f"{f}.cidr", "network_too_broad",
                f"{n.cidr} is very large ({net.num_addresses:,} addresses). Confirm this network is really yours.",
                f"{n.cidr} is erg groot ({net.num_addresses:,} adressen). Bevestig dat dit netwerk echt van jou is.")

    # ---- extra relay listeners ----------------------------------------------
    ports_seen = set()
    for i, p in enumerate(cfg.relay_ports):
        f = f"relay_ports[{i}]"
        port = int(p.port)
        if not 1 <= port <= 65535:
            add(f"{f}.port", "port_invalid", "Port must be between 1 and 65535.", "Poort moet tussen 1 en 65535 liggen.")
        elif port in RESERVED_PORTS:
            add(f"{f}.port", "port_reserved", f"Port {port} is already used by the Mailfilter itself.",
                f"Poort {port} wordt al door de Mailfilter zelf gebruikt.")
        if port in ports_seen:
            add(f"{f}.port", "port_duplicate", f"Port {port} is listed twice.", f"Poort {port} staat er twee keer in.")
        ports_seen.add(port)
        if p.tls not in LISTENER_TLS:
            add(f"{f}.tls", "tls_invalid", f"TLS must be one of {', '.join(LISTENER_TLS)}.",
                f"TLS moet een van {', '.join(LISTENER_TLS)} zijn.")

    # ---- outbound destination throttling ------------------------------------
    pol_seen = set()
    for i, p in enumerate(cfg.destination_policies):
        f = f"destination_policies[{i}]"
        name = normalise_domain(p.domain)
        if not name:
            add(f"{f}.domain", "domain_invalid", f"'{p.domain}' is not a valid domain name.",
                f"'{p.domain}' is geen geldige domeinnaam.")
            continue
        p.domain = name
        if name in all_names:
            add(f"{f}.domain", "policy_is_relay_domain",
                f"{name} is a domain on this Mailfilter; set its limits on the domain itself.",
                f"{name} is een domein op deze Mailfilter; stel de limieten bij het domein zelf in.")
        if name in pol_seen:
            add(f"{f}.domain", "domain_duplicate", f"{name} is listed twice.", f"{name} staat er twee keer in.")
        pol_seen.add(name)
        _check_queue(add, f"{f}.queue", p.queue)

    # ---- paused senders ------------------------------------------------------
    holds_seen = set()
    for i, h in enumerate(cfg.holds):
        f = f"holds[{i}]"
        h.value = (h.value or "").strip().lower()
        h.reason = re.sub(r"[^\w .,:;()/@+-]", "", h.reason or "")[:200]
        if h.kind == "ip":
            ip = _ip(h.value)
            if ip is None:
                add(f"{f}.value", "hold_invalid", f"'{h.value}' is not an IP address.", f"'{h.value}' is geen IP-adres.")
                continue
            if ip.is_loopback or ip.is_unspecified:
                add(f"{f}.value", "hold_local",
                    "The Mailfilter's own address cannot be paused (its alerts would stop too).",
                    "Het eigen adres van de Mailfilter kan niet gepauzeerd worden (de meldingen zouden ook stoppen).")
            h.value = str(ip)
        elif h.kind == "account":
            if not re.fullmatch(r"[^\s:#]{1,320}", h.value):
                add(f"{f}.value", "hold_invalid", f"'{h.value}' is not an SMTP account name.",
                    f"'{h.value}' is geen SMTP-accountnaam.")
        else:
            add(f"{f}.kind", "hold_invalid", "Unknown kind of sender.", "Onbekend soort afzender.")
        if (h.kind, h.value) in holds_seen:
            add(f"{f}.value", "hold_duplicate", f"{h.value} is already paused.", f"{h.value} is al gepauzeerd.")
        holds_seen.add((h.kind, h.value))

    dft = cfg.defaults
    for attr in ("relay_ip_per_minute", "relay_ip_per_hour", "domain_per_hour", "domain_per_day",
                 "user_per_hour", "concurrency"):
        if int(getattr(dft, attr)) < 1:
            add(f"defaults.{attr}", "limit_invalid", "Limit must be a positive number.",
                "Limiet moet een positief getal zijn.")
    return problems


def _check_queue(add, f: str, q) -> None:
    if q.concurrency is not None and not 1 <= int(q.concurrency) <= 100:
        add(f"{f}.concurrency", "concurrency_invalid", "Parallel connections must be between 1 and 100.",
            "Gelijktijdige verbindingen moet tussen 1 en 100 liggen.")
    if q.rate_delay is not None and not 0 <= int(q.rate_delay) <= 3600:
        add(f"{f}.rate_delay", "rate_delay_invalid", "Delay must be between 0 and 3600 seconds.",
            "Vertraging moet tussen 0 en 3600 seconden liggen.")


def check(cfg: GatewayConfig) -> None:
    """Raise ConfigError when the configuration has problems."""
    problems = validate(cfg)
    if problems:
        raise ConfigError(problems)
