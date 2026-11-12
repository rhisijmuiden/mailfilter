"""ORM models. Each feature keeps its models in its own module; import them here
so Alembic sees every table."""

from .apikeys import ApiKey
from .core import AppSetting, AuditLog, AuthSession, Domain, User, user_domain
from .dmarc import DmarcIngest, DmarcReport, DmarcRow
from .feedback import SpamFeedback
from .gateway import AddressRoute, DestinationPolicy, OutboundHold, DomainDestination, DomainRoute, RelayNetwork, RelayPort, SmtpAccount, TlsPolicy
from .cluster import ClusterNode, ClusterNonce
from .mailtrap import MailTrap, MailTrapNetwork, mailtrap_user
from .lists import DigestDomain, DigestSent, SenderListEntry
from .reports import MonthlyReportUser, MonthlyStats
from .trace import QuarantineAction, TraceMessage, TraceRecipient

__all__ = ["ApiKey", "AppSetting", "AuditLog", "AuthSession", "Domain", "User", "user_domain", "QuarantineAction", "TraceMessage", "TraceRecipient",
           "DmarcIngest", "DmarcReport", "DmarcRow",
           "AddressRoute", "DestinationPolicy", "OutboundHold", "DomainDestination", "DomainRoute", "RelayNetwork", "RelayPort", "SmtpAccount", "TlsPolicy",
           "ClusterNode", "ClusterNonce",
           "DigestDomain", "DigestSent", "SenderListEntry", "MailTrap", "MailTrapNetwork", "mailtrap_user",
           "MonthlyReportUser", "MonthlyStats", "SpamFeedback"]
