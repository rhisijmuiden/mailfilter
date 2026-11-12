"""Neutral description of what a PMG backup contains.

Everything here is plain data, so the web app can show it as a preview and
store it as JSON between the "upload" and the "apply" step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Transport:
    """Where Mailfilter delivers mail for a domain (PMG "Transports")."""

    host: str
    port: int = 25
    protocol: str = "smtp"  # smtp or lmtp
    use_mx: bool = False  # True: look up MX of host; False: deliver to host directly
    comment: str = ""


@dataclass
class Domain:
    """A domain Mailfilter accepts mail for (PMG "Relay Domains")."""

    name: str
    comment: str = ""
    transport: Transport | None = None
    dkim_sign: bool = False
    require_tls_inbound: bool = False


@dataclass
class AddressTransport:
    """A transport for a single address or a domain that is not a relay domain."""

    match: str
    transport: Transport


@dataclass
class RelayNetwork:
    """An IP address or network that may relay through Mailfilter (PMG "Trusted Networks")."""

    cidr: str
    comment: str = ""


@dataclass
class ListEntry:
    """One entry of a welcome- or blocklist.

    ``kind`` is one of: email, domain, ip, network, regex, receiver,
    receiver_domain, receiver_regex, ldap, ldap_user.
    """

    kind: str
    value: str
    list: str  # "welcome" or "block"
    scope: str = "global"  # "global", "smtp" (before filtering) or "user:<address>"
    source: str = ""  # PMG object group it came from


@dataclass
class TlsPolicy:
    destination: str
    policy: str


@dataclass
class Dkim:
    selector: str
    sign_all_mail: bool = False
    private_key_pem: str | None = None
    domains: list[str] = field(default_factory=list)


@dataclass
class RuleSummary:
    """A PMG filter rule. Rules are shown for review, not converted automatically."""

    name: str
    priority: int
    active: bool
    direction: str  # in, out, both
    from_groups: list[str] = field(default_factory=list)
    to_groups: list[str] = field(default_factory=list)
    what_groups: list[str] = field(default_factory=list)
    when_groups: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    is_default: bool = False
    handled_by: str = ""  # what Mailfilter does with it, empty if manual review needed
    handled_by_nl: str = ""


@dataclass
class Warning:
    code: str
    message: str
    message_nl: str = ""


@dataclass
class ImportPlan:
    source_product: str = ""
    source_version: str = ""
    backup_time: str = ""
    hostname: str = ""
    admin_email: str = ""
    domains: list[Domain] = field(default_factory=list)
    address_transports: list[AddressTransport] = field(default_factory=list)
    relay_networks: list[RelayNetwork] = field(default_factory=list)
    list_entries: list[ListEntry] = field(default_factory=list)
    tls_policies: list[TlsPolicy] = field(default_factory=list)
    dkim: Dkim | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    raw_config: dict[str, dict[str, str]] = field(default_factory=dict)
    rules: list[RuleSummary] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)

    def warn(self, code: str, message: str, message_nl: str = "") -> None:
        self.warnings.append(Warning(code, message, message_nl or message))

    def to_dict(self, include_secrets: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if data.get("dkim") and not include_secrets:
            key = data["dkim"].get("private_key_pem")
            data["dkim"]["private_key_pem"] = "(present, hidden)" if key else None
        return data

    def summary(self) -> dict[str, int]:
        """Counts for the preview header."""
        welcome = sum(1 for e in self.list_entries if e.list == "welcome")
        return {
            "domains": len(self.domains),
            "relay_networks": len(self.relay_networks),
            "transports": sum(1 for d in self.domains if d.transport) + len(self.address_transports),
            "welcomelist": welcome,
            "blocklist": len(self.list_entries) - welcome,
            "tls_policies": len(self.tls_policies),
            "dkim_domains": len(self.dkim.domains) if self.dkim else 0,
            "rules_to_review": sum(1 for r in self.rules if not r.handled_by),
            "warnings": len(self.warnings),
        }
