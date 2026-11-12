"""Domains, relay access, extra relay ports and throttling for the Mailfilter gateway.

Framework-free: the web app builds a GatewayConfig from its database and
calls Applier().apply(); the installer and admins use `python3 -m mailfilter_config`.
"""
from .apply import Applier, ApplyError, Report, load_state
from .model import (AddressRoute, CloudTenant, Defaults, Destination, DestinationPolicy, Domain, GatewayConfig, MailTrap, OutboundHold, OutboundLimits, SmtpAccount,
                    QueuePolicy, RelayNetwork, RelayPort, TlsPolicy)
from .render import render
from .rules import RULES, CustomRule
from .validate import ConfigError, Problem, check, validate

__all__ = ["CloudTenant", "OutboundHold", "AddressRoute", "TlsPolicy", "Applier", "ApplyError", "Report", "load_state", "Defaults", "Destination", "DestinationPolicy",
           "Domain", "GatewayConfig", "MailTrap", "OutboundLimits", "SmtpAccount", "QueuePolicy", "RelayNetwork", "RelayPort",
           "render", "RULES", "CustomRule", "ConfigError", "Problem", "check", "validate"]
