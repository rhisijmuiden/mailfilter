"""Who is sending: turn an IP and its reverse DNS name into a name people know.

The name is worked out when the dashboard is shown (not stored), so adding a
service to KNOWN also fixes the names in older reports.
"""

from __future__ import annotations

import ipaddress

# Host name suffix -> service. Longest match wins.
KNOWN: dict[str, str] = {
    "google.com": "Google", "googlemail.com": "Google", "1e100.net": "Google",
    "outlook.com": "Microsoft 365", "protection.outlook.com": "Microsoft 365", "hotmail.com": "Microsoft",
    "amazonses.com": "Amazon SES", "smtp-out.amazonses.com": "Amazon SES",
    "sendgrid.net": "SendGrid", "mcsv.net": "Mailchimp", "rsgsv.net": "Mailchimp", "mcdlv.net": "Mailchimp",
    "mandrillapp.com": "Mailchimp Transactional", "mailgun.net": "Mailgun", "mailgun.org": "Mailgun",
    "sparkpostmail.com": "SparkPost", "mtasv.net": "Postmark", "sendinblue.com": "Brevo", "brevo.com": "Brevo",
    "hubspotemail.net": "HubSpot", "exacttarget.com": "Salesforce Marketing Cloud", "salesforce.com": "Salesforce",
    "zendesk.com": "Zendesk", "freshdesk.com": "Freshdesk", "mailjet.com": "Mailjet", "emsmtp.com": "Mailjet",
    "transip.email": "TransIP", "transip.nl": "TransIP", "mijndomein.nl": "Mijndomein", "hostnet.nl": "Hostnet",
    "yahoo.com": "Yahoo", "yahoodns.net": "Yahoo", "zoho.com": "Zoho", "zohomail.com": "Zoho",
    "icloud.com": "Apple iCloud", "apple.com": "Apple", "messagingengine.com": "Fastmail",
    "laposte.net": "La Poste", "ovh.net": "OVHcloud", "mimecast.com": "Mimecast", "pphosted.com": "Proofpoint",
    "ppe-hosted.com": "Proofpoint", "messagelabs.com": "Broadcom Email Security", "barracudanetworks.com": "Barracuda",
    "exclaimer.net": "Exclaimer", "codetwo.com": "CodeTwo", "activecampaign.com": "ActiveCampaign",
    "acems1.com": "ActiveCampaign", "cmail1.com": "Campaign Monitor", "cmail2.com": "Campaign Monitor",
    "createsend.com": "Campaign Monitor", "klaviyomail.com": "Klaviyo", "shopify.com": "Shopify",
    "squarespace.com": "Squarespace", "wix.com": "Wix", "mailerlite.com": "MailerLite", "elasticemail.com": "Elastic Email",
    "smtp2go.com": "SMTP2GO", "smtp.com": "SMTP.com", "socketlabs.com": "SocketLabs", "mailchannels.net": "MailChannels",
    "proton.ch": "Proton Mail", "protonmail.ch": "Proton Mail", "gmx.net": "GMX", "web.de": "WEB.DE", "kpnmail.nl": "KPN",
    "ziggo.nl": "Ziggo", "xs4all.net": "XS4ALL", "one.com": "one.com", "strato.de": "Strato", "ionos.com": "IONOS",
    "kundenserver.de": "IONOS", "hetzner.com": "Hetzner", "your-server.de": "Hetzner",
}

# Second-level labels under which the registrable domain has three labels (co.uk and friends)
_TWO_PART = {"co", "com", "net", "org", "gov", "ac", "edu", "ne", "or"}


def base_domain(host: str) -> str:
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and labels[-2] in _TWO_PART and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def identify(ip: str, host: str, own_ips: set[str]) -> tuple[str, str]:
    """Returns (key, kind). kind is own, known, host or unknown."""
    try:
        if str(ipaddress.ip_address(ip)) in own_ips:
            return "Mailfilter", "own"
    except ValueError:
        pass
    host = (host or "").lower().rstrip(".")
    if host:
        best = ""
        for suffix in KNOWN:
            if (host == suffix or host.endswith("." + suffix)) and len(suffix) > len(best):
                best = suffix
        if best:
            return KNOWN[best], "known"
        return base_domain(host), "host"
    return ip, "unknown"


def normalize_ips(addresses: list[str]) -> set[str]:
    out = set()
    for a in addresses:
        try:
            out.add(str(ipaddress.ip_address(a.split("/")[0])))
        except ValueError:
            continue
    return out
