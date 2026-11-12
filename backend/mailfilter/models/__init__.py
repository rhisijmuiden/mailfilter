"""ORM models. Each feature keeps its models in its own module; import them here
so Alembic sees every table."""

from .core import AppSetting, AuditLog, AuthSession, Domain, User, user_domain
from .dmarc import DmarcIngest, DmarcReport, DmarcRow
from .gateway import AddressRoute, DestinationPolicy, OutboundHold, DomainDestination, DomainRoute, RelayNetwork, RelayPort, TlsPolicy
from .lists import DigestDomain, DigestSent, SenderListEntry
from .trace import QuarantineAction, TraceMessage, TraceRecipient

__all__ = ["AppSetting", "AuditLog", "AuthSession", "Domain", "User", "user_domain", "QuarantineAction", "TraceMessage", "TraceRecipient",
           "DmarcIngest", "DmarcReport", "DmarcRow",
           "AddressRoute", "DestinationPolicy", "OutboundHold", "DomainDestination", "DomainRoute", "RelayNetwork", "RelayPort", "TlsPolicy",
           "DigestDomain", "DigestSent", "SenderListEntry"]
