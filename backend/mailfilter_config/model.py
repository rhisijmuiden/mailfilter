"""Desired state of the gateway: domains, relay access, listeners and throttling.

This is the single description the web app (from its database) and the CLI
(from /etc/mailfilter/gateway.json) hand to the renderer. It has no
framework dependencies so it can run as root from a small sudo helper.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

TLS_MODES = ("may", "encrypt", "verify")
ALL_TLS_MODES = ("none", "may", "encrypt", "dane", "verify", "secure")
PROTOCOLS = ("smtp", "lmtp")
LISTENER_TLS = ("may", "encrypt", "wrapper")


@dataclass
class Destination:
    """Where clean mail for a domain is delivered."""

    host: str
    port: int = 25
    # False: deliver to exactly this host ("[host]:port"); True: look up its MX records.
    mx: bool = False
    protocol: str = "smtp"  # smtp or lmtp

    def nexthop(self) -> str:
        host = self.host if self.mx else f"[{self.host}]"
        return f"{host}:{self.port}"


@dataclass
class QueuePolicy:
    """Delivery limits for one queue (one Postfix transport)."""

    concurrency: int | None = None  # parallel connections to the destination
    rate_delay: int | None = None  # seconds between deliveries (forces 1 connection)


@dataclass
class OutboundLimits:
    """How much a domain may send out through the gateway (Rspamd ratelimit)."""

    per_hour: int | None = None
    per_day: int | None = None


@dataclass
class Domain:
    name: str
    destinations: list[Destination]
    enabled: bool = True
    customer: str | None = None
    tls: str = "may"
    verify_recipients: bool = True
    queue: QueuePolicy = field(default_factory=QueuePolicy)
    outbound: OutboundLimits = field(default_factory=OutboundLimits)
    note: str = ""


@dataclass
class RelayNetwork:
    """An IP address or network that may send mail out through the gateway."""

    cidr: str
    description: str = ""
    customer: str | None = None
    enabled: bool = True
    # Networks larger than /16 (IPv4) or /32 (IPv6) are refused unless set.
    allow_broad: bool = False


@dataclass
class RelayPort:
    """Extra relay listener next to 587 and 465."""

    port: int
    tls: str = "may"  # may: STARTTLS optional, encrypt: STARTTLS required, wrapper: implicit TLS
    description: str = ""


@dataclass
class DestinationPolicy:
    """Throttling for outbound mail to a remote domain (e.g. a big mailbox provider)."""

    domain: str
    queue: QueuePolicy = field(default_factory=QueuePolicy)
    note: str = ""


@dataclass
class AddressRoute:
    """Deliver one address (or a domain that is not a relay domain) somewhere else."""

    address: str  # user@example.nl or example.org
    destination: Destination
    note: str = ""


@dataclass
class TlsPolicy:
    """TLS requirement for delivery to one destination (Postfix smtp_tls_policy_maps)."""

    destination: str  # example.nl, .example.nl or [host]:port
    level: str = "encrypt"  # none, may, encrypt, dane, verify, secure
    note: str = ""


@dataclass
class Defaults:
    relay_ip_per_minute: int = 30
    relay_ip_per_hour: int = 1000
    domain_per_hour: int = 1000
    domain_per_day: int = 8000
    user_per_hour: int = 500
    concurrency: int = 10


@dataclass
class GatewayConfig:
    domains: list[Domain] = field(default_factory=list)
    relay_networks: list[RelayNetwork] = field(default_factory=list)
    relay_ports: list[RelayPort] = field(default_factory=list)
    destination_policies: list[DestinationPolicy] = field(default_factory=list)
    address_routes: list[AddressRoute] = field(default_factory=list)
    tls_policies: list[TlsPolicy] = field(default_factory=list)
    defaults: Defaults = field(default_factory=Defaults)
    # Used for loop detection; filled in from the server when empty.
    hostname: str = ""
    own_addresses: list[str] = field(default_factory=list)
    version: int = 1

    # ---- (de)serialisation -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GatewayConfig":
        def queue(d: dict | None) -> QueuePolicy:
            return QueuePolicy(**(d or {}))

        domains = []
        for d in data.get("domains", []):
            d = dict(d)
            dests = [Destination(**x) for x in d.pop("destinations", [])]
            q = queue(d.pop("queue", None))
            o = OutboundLimits(**(d.pop("outbound", None) or {}))
            domains.append(Domain(destinations=dests, queue=q, outbound=o, **d))
        policies = []
        for p in data.get("destination_policies", []):
            p = dict(p)
            policies.append(DestinationPolicy(queue=queue(p.pop("queue", None)), **p))
        return cls(
            domains=domains,
            relay_networks=[RelayNetwork(**n) for n in data.get("relay_networks", [])],
            relay_ports=[RelayPort(**p) for p in data.get("relay_ports", [])],
            destination_policies=policies,
            address_routes=[AddressRoute(destination=Destination(**dict(a)["destination"]),
                                         **{k: v for k, v in a.items() if k != "destination"})
                            for a in data.get("address_routes", [])],
            tls_policies=[TlsPolicy(**t) for t in data.get("tls_policies", [])],
            defaults=Defaults(**data.get("defaults", {})),
            hostname=data.get("hostname", ""),
            own_addresses=list(data.get("own_addresses", [])),
            version=data.get("version", 1),
        )

    @classmethod
    def from_json(cls, text: str) -> "GatewayConfig":
        return cls.from_dict(json.loads(text))
