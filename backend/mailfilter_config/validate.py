"""Checks a GatewayConfig before anything touches the server.

Every problem has a stable code (for the web app), the field it belongs to,
and a message in English and Dutch. Validation also normalises values in
place (lower-case domains, canonical networks), so render() gets clean input.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from .rules import problems as rule_problems
from .model import ALL_TLS_MODES, DOMAIN_KINDS, LISTENER_TLS, MAILTRAP_MODES, PROTOCOLS, TLS_MODES, GatewayConfig

# Ports the gateway itself uses; an extra relay listener may not take them.
RESERVED_PORTS = {22, 25, 53, 80, 443, 465, 587, 993, 3310, 5432, 6379, 11332, 11333, 11334, 11335}
# Where the gateway accepts mail itself: delivering to these on a local address is a loop.
OWN_SMTP_PORTS = {25, 465, 587}
MIN_PREFIX = {4: 16, 6: 32}

USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._@-]{0,99}")
# Dovecot password scheme prefix plus hash; never a colon or line break (passwd-file format).
HASH_RE = re.compile(r"\{[A-Z0-9.-]{2,20}\}[A-Za-z0-9+/=$.]{8,300}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+")
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
        if d.kind not in DOMAIN_KINDS:
            add(f"{f}.kind", "kind_invalid", "A domain is either a transport domain or a relay domain.",
                "Een domein is een transport-domein of een relay-domein.")
            continue
        if d.kind == "relay":
            # Outbound only: nothing is delivered, so there is no mail server to check.
            if d.destinations:
                add(f"{f}.destinations", "relay_has_destination",
                    f"{name} is a relay domain (outbound only); it has no mail server to deliver to.",
                    f"{name} is een relay-domein (alleen uitgaand); het heeft geen mailserver om op af te leveren.")
            for attr in ("per_hour", "per_day"):
                v = getattr(d.outbound, attr)
                if v is not None and not 1 <= int(v) <= 10_000_000:
                    add(f"{f}.outbound.{attr}", "limit_invalid", "Limit must be a positive number.",
                        "Limiet moet een positief getal zijn.")
            continue
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
        senders = []
        for d in n.senders or []:
            nd = normalise_domain(str(d))
            if not nd:
                add(f"{f}.senders", "sender_domain_invalid", f"'{d}' is not a valid domain name.",
                    f"'{d}' is geen geldige domeinnaam.")
            elif nd not in senders:
                senders.append(nd)
        n.senders = sorted(senders)
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

    # ---- SMTP accounts ---------------------------------------------------------
    domain_names = {d.name for d in cfg.domains}
    users_seen: set[str] = set()
    for i, a in enumerate(cfg.smtp_accounts):
        f = f"smtp_accounts[{i}]"
        a.username = (a.username or "").strip().lower()
        a.domain = normalise_domain(a.domain) or (a.domain or "")
        a.description = re.sub(r"[\r\n:]+", " ", a.description or "").strip()[:200]
        if not USERNAME_RE.fullmatch(a.username):
            add(f"{f}.username", "account_invalid",
                f"'{a.username}' is not a valid account name (letters, digits and . _ - @, at most 100).",
                f"'{a.username}' is geen geldige accountnaam (letters, cijfers en . _ - @, maximaal 100).")
        if a.username in users_seen:
            add(f"{f}.username", "account_duplicate", f"{a.username} is listed twice.",
                f"{a.username} staat er twee keer in.")
        users_seen.add(a.username)
        if a.domain not in domain_names:
            add(f"{f}.domain", "account_domain_unknown", f"{a.domain} is not a domain on this Mailfilter.",
                f"{a.domain} is geen domein op deze Mailfilter.")
        if not HASH_RE.fullmatch(a.password_hash or ""):
            add(f"{f}.password_hash", "account_hash_invalid", "The password could not be stored.",
                "Het wachtwoord kon niet worden opgeslagen.")

    _check_mailtraps(cfg, add, own_ips)
    _check_cloud_tenants(cfg, add)

    for i, name in enumerate(cfg.archive_domains):
        if normalise_domain(str(name)) != str(name).strip().lower():
            add(f"archive_domains[{i}]", "archive_domain_invalid", f"'{name}' is not a valid domain name.",
                f"'{name}' is geen geldige domeinnaam.")

    dft = cfg.defaults
    for attr in ("relay_ip_per_minute", "relay_ip_per_hour", "domain_per_hour", "domain_per_day",
                 "user_per_hour", "concurrency"):
        if int(getattr(dft, attr)) < 1:
            add(f"defaults.{attr}", "limit_invalid", "Limit must be a positive number.",
                "Limiet moet een positief getal zijn.")
    for p in rule_problems(cfg.custom_rules, {d.name for d in cfg.domains}):
        add(*p)
    return problems


def normalise_allow(entry: str) -> str | None:
    """"user@example.nl" or "@example.nl" (the whole domain), lower-case; None when invalid."""
    local, at, dom = (entry or "").strip().lower().rpartition("@")
    ndom = normalise_domain(dom)
    if not at or not ndom or (local and not EMAIL_RE.fullmatch(local)):
        return None
    return f"{local}@{ndom}"


def _check_cloud_tenants(cfg: GatewayConfig, add) -> None:
    from .cloud import PROVIDERS, TENANT_ID_RE

    kinds = {d.name: d.kind for d in cfg.domains}
    seen: set[str] = set()
    for i, t in enumerate(cfg.cloud_tenants):
        f = f"cloud_tenants[{i}]"
        if t.provider not in PROVIDERS:
            add(f"{f}.provider", "cloud_provider_invalid", "Choose Microsoft 365 or Google Workspace.",
                "Kies Microsoft 365 of Google Workspace.")
            continue
        name = normalise_domain(t.domain or "")
        if not name:
            add(f"{f}.domain", "domain_invalid", f"'{t.domain}' is not a valid domain name.",
                f"'{t.domain}' is geen geldige domeinnaam.")
            continue
        t.domain = name
        if name in seen:
            add(f"{f}.domain", "cloud_duplicate", f"{name} is already linked to a cloud mail tenant.",
                f"{name} is al aan een cloud-mailomgeving gekoppeld.")
        seen.add(name)
        if name not in kinds:
            add(f"{f}.domain", "cloud_domain_missing", f"Add {name} under Domains first.",
                f"Voeg {name} eerst toe onder Domeinen.")
        t.tenant_id = (t.tenant_id or "").strip().lower()
        if t.provider == "m365" and not TENANT_ID_RE.fullmatch(t.tenant_id):
            add(f"{f}.tenant_id", "tenant_id_invalid",
                "A Microsoft 365 tenant ID looks like 8c1f2b6e-0d3a-4c55-9e1b-6f0a2d7be42a.",
                "Een Microsoft 365 tenant-ID ziet eruit als 8c1f2b6e-0d3a-4c55-9e1b-6f0a2d7be42a.")
        if t.provider == "google":
            t.tenant_id = ""


def _check_mailtraps(cfg: GatewayConfig, add, own_ips) -> None:
    relay_nets = []
    for n in cfg.relay_networks:
        try:
            relay_nets.append((n.cidr, ipaddress.ip_network(n.cidr, strict=False)))
        except ValueError:
            pass
    accounts = {a.username for a in cfg.smtp_accounts}
    slugs: set[str] = set()
    logins: set[str] = set()
    trap_nets: dict[str, str] = {}
    for i, t in enumerate(cfg.mailtraps):
        f = f"mailtraps[{i}]"
        t.slug = (t.slug or "").strip().lower()
        t.name = re.sub(r"[\r\n]+", " ", t.name or "").strip()[:100]
        label = t.name or t.slug
        if not SLUG_RE.fullmatch(t.slug):
            add(f"{f}.slug", "mailtrap_slug_invalid", "The Mailtrap mailbox needs a short name (letters, digits, -).",
                "Het Mailtrap-postvak heeft een korte naam nodig (letters, cijfers, -).")
        elif t.slug in slugs:
            add(f"{f}.slug", "mailtrap_duplicate", f"{t.slug} is listed twice.", f"{t.slug} staat er twee keer in.")
        slugs.add(t.slug)

        cleaned = []
        for j, raw in enumerate(t.networks):
            nf = f"{f}.networks[{j}]"
            try:
                net = ipaddress.ip_network(str(raw).strip(), strict=False)
            except ValueError:
                add(nf, "network_invalid", f"'{raw}' is not an IP address or network.",
                    f"'{raw}' is geen IP-adres of netwerk.")
                continue
            cidr = str(net.network_address) if net.prefixlen == net.max_prefixlen else str(net)
            cleaned.append(cidr)
            if net.prefixlen < MIN_PREFIX[net.version]:
                add(nf, "network_too_broad", f"{cidr} is too large for a test environment.",
                    f"{cidr} is te groot voor een testomgeving.")
            if net.network_address.is_loopback or any(ip in net for ip in own_ips):
                add(nf, "mailtrap_own_address",
                    f"{cidr} contains the Mailfilter itself; its own mail (alerts, reports) would be caught.",
                    f"{cidr} bevat de Mailfilter zelf; de eigen mail (meldingen, rapporten) zou dan worden opgevangen.")
            for rc, rnet in relay_nets:
                if rnet.version == net.version and rnet.overlaps(net):
                    add(nf, "mailtrap_network_is_relay",
                        f"{cidr} is also in Relay access ({rc}). An address either relays or goes to the Mailtrap; "
                        "remove it from Relay access first.",
                        f"{cidr} staat ook bij Relay-toegang ({rc}). Een adres relayt óf gaat naar de Mailtrap; "
                        "haal het eerst weg bij Relay-toegang.")
            if cidr in trap_nets:
                add(nf, "network_duplicate", f"{cidr} is already in Mailtrap mailbox {trap_nets[cidr]}.",
                    f"{cidr} staat al in Mailtrap-postvak {trap_nets[cidr]}.")
            trap_nets.setdefault(cidr, label)
        t.networks = cleaned

        # The login also names the mailbox directory, so every mailbox has one.
        t.login = (t.login or "").strip().lower() or f"trap-{t.slug}"
        if True:
            if not USERNAME_RE.fullmatch(t.login):
                add(f"{f}.login", "account_invalid",
                    f"'{t.login}' is not a valid login name (letters, digits and . _ - @, at most 100).",
                    f"'{t.login}' is geen geldige inlognaam (letters, cijfers en . _ - @, maximaal 100).")
            elif t.login in logins or t.login in accounts:
                add(f"{f}.login", "account_duplicate", f"The login {t.login} is already in use.",
                    f"De inlognaam {t.login} is al in gebruik.")
            logins.add(t.login)
        if (t.smtp_login or t.imap) and not HASH_RE.fullmatch(t.password_hash or ""):
            add(f"{f}.password_hash", "account_hash_invalid", "The password could not be stored.",
                "Het wachtwoord kon niet worden opgeslagen.")

        if t.mode not in MAILTRAP_MODES:
            add(f"{f}.mode", "mailtrap_mode_invalid", "Choose to catch all mail or all except testers.",
                "Kies alles opvangen of alles behalve testers.")
        allow = []
        for entry in t.allow:
            norm = normalise_allow(entry)
            if norm is None:
                add(f"{f}.allow", "address_invalid", f"'{entry}' is not an email address or @domain.",
                    f"'{entry}' is geen e-mailadres of @domein.")
            elif norm not in allow:
                allow.append(norm)
        t.allow = allow
        if not 1 <= int(t.retention_days) <= 365:
            add(f"{f}.retention_days", "retention_invalid", "Keep mail between 1 and 365 days.",
                "Bewaar mail tussen 1 en 365 dagen.")
        if not 1 <= int(t.max_messages) <= 100_000:
            add(f"{f}.max_messages", "max_messages_invalid", "The maximum must be between 1 and 100,000 messages.",
                "Het maximum moet tussen 1 en 100.000 mails liggen.")


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
