"""Microsoft 365 and Google Workspace tenants behind the Mailfilter.

A tenant sends its outgoing mail to the Mailfilter (outbound connector /
outbound gateway) from the provider's shared mail servers. Those servers are
used by every customer of the provider, so they are NOT trusted relay
networks. Instead:

* Postfix lets them relay only with an envelope sender of a domain that has a
  tenant of that provider (or the empty bounce sender); see render.py,
  cloud_relay.cidr and the mfc_* restriction classes.
* Rspamd (rspamd/mailfilter_cloud.lua) then checks that relayed mail really
  comes from that tenant: for Microsoft 365 the X-MS-Exchange-CrossTenant-Id
  header (set by Exchange Online) must be the domain's tenant ID; for Google
  the message must carry a valid DKIM signature of the domain. Otherwise the
  message is refused, so another customer of the same provider cannot send
  through this Mailfilter in the name of your domain.

The address lists are the providers' published outbound ranges. They change
rarely; they are updated together with Mailfilter.
"""

from __future__ import annotations

import re
from pathlib import Path

PROVIDERS = ("m365", "google")

# Exchange Online Protection, "Microsoft 365 URLs and IP address ranges", ID 9/10.
M365_NETWORKS = [
    "40.92.0.0/15", "40.107.0.0/16", "52.100.0.0/15", "52.102.0.0/16", "52.103.0.0/16", "104.47.0.0/17",
    "2a01:111:f400::/48", "2a01:111:f403::/48",
]

# _spf.google.com (_netblocks, _netblocks2, _netblocks3).
GOOGLE_NETWORKS = [
    "35.190.247.0/24", "64.233.160.0/19", "66.102.0.0/20", "66.249.80.0/20", "72.14.192.0/18", "74.125.0.0/16",
    "108.177.8.0/21", "173.194.0.0/16", "209.85.128.0/17", "216.58.192.0/19", "216.239.32.0/19",
    "172.217.0.0/19", "172.217.32.0/20", "172.217.128.0/19", "172.217.160.0/20", "172.217.192.0/19",
    "172.253.56.0/21", "172.253.112.0/20", "108.177.96.0/19", "35.191.0.0/16", "130.211.0.0/22",
    "2001:4860:4000::/36", "2404:6800:4000::/36", "2607:f8b0:4000::/36", "2800:3f0:4000::/36",
    "2a00:1450:4000::/36", "2c0f:fb50:4000::/36",
]

NETWORKS = {"m365": M365_NETWORKS, "google": GOOGLE_NETWORKS}

# Where clean inbound mail goes. Microsoft: the tenant's own MX host.
GOOGLE_DESTINATIONS = ["aspmx.l.google.com", "alt1.aspmx.l.google.com", "alt2.aspmx.l.google.com",
                       "alt3.aspmx.l.google.com", "alt4.aspmx.l.google.com"]

TENANT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

LUA_PATH = "/etc/rspamd/lua.local.d/mailfilter_cloud.lua"
LUA_SOURCE = Path(__file__).with_name("rspamd") / "mailfilter_cloud.lua"
SETTINGS_PATH = "/etc/rspamd/mailfilter_cloud.json"
CIDR_MAP = "/etc/mailfilter/postfix/cloud_relay.cidr"


def m365_destination(domain: str) -> str:
    """The MX host Microsoft gives a domain: rhis.nl -> rhis-nl.mail.protection.outlook.com.

    Only a guess for new tenants (Microsoft may add a suffix, and newer tenants
    get *.mx.microsoft); the wizard prefers the MX that is actually published."""
    return f"{domain.replace('.', '-')}.mail.protection.outlook.com"


def detect_provider(mx_hosts: list[str]) -> str | None:
    """m365 / google from a domain's current MX hosts, None if neither."""
    for h in mx_hosts:
        h = h.lower().rstrip(".")
        if h.endswith(".mail.protection.outlook.com") or h.endswith(".mx.microsoft"):
            return "m365"
        if h.endswith("aspmx.l.google.com") or h.endswith(".googlemail.com") or h == "smtp.google.com":
            return "google"
    return None
