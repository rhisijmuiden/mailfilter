"""Custom filter rules (Configuration > Custom rules): the catalog with the
texts the web interface shows, validation, and the files Rspamd reads.

Each rule can be switched on or off globally and per domain. The rules
themselves run in Rspamd (rspamd/mailfilter_rules.lua, installed as
/etc/rspamd/lua.local.d/mailfilter_rules.lua); this module writes their
settings to /etc/rspamd/mailfilter_rules.json. A new rule needs an entry in
RULES and code in the Lua file.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LUA_PATH = "/etc/rspamd/lua.local.d/mailfilter_rules.lua"
SETTINGS_PATH = "/etc/rspamd/mailfilter_rules.json"
LUA_SOURCE = Path(__file__).with_name("rspamd") / "mailfilter_rules.lua"
PROTECTION_LUA_PATH = "/etc/rspamd/lua.local.d/mailfilter_protection.lua"
PROTECTION_LUA_SOURCE = Path(__file__).with_name("rspamd") / "mailfilter_protection.lua"
ARC_CONF_PATH = "/etc/rspamd/local.d/arc.conf"
ARC_TRUSTED_MAP = "/etc/mailfilter/rspamd/arc_trusted.map"


@dataclass
class CustomRule:
    """What the admin chose for one rule. `domains` overrides `enabled` per domain."""

    enabled: bool
    options: dict[str, Any] = field(default_factory=dict)
    domains: dict[str, bool] = field(default_factory=dict)


def _t(en: str, nl: str) -> dict[str, str]:
    return {"en": en, "nl": nl}


# Option types: "score" (number 0-20), "bool", "text", "choice" (one of
# "choices"), "list" (strings of kind "item": extension, domain or name).
# A rule with "per_domain": False only has the global switch.
RULES: list[dict[str, Any]] = [
    {
        "id": "newsletter_header",
        "group": "newsletter",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_NEWSLETTER",
        "title": _t("Mark newsletters", "Nieuwsbrieven markeren"),
        "summary": _t(
            "Adds the header X-Mailfilter-Newsletter to incoming newsletters, so mail programs can sort them into a folder.",
            "Zet de header X-Mailfilter-Newsletter op binnenkomende nieuwsbrieven, zodat mailprogramma's ze in een map kunnen sorteren."),
        "details": _t(
            "A message counts as a newsletter when it has a List-Unsubscribe or List-Id header, 'Precedence: bulk', a "
            "campaign header of an e-mail marketing platform, or was sent with a known newsletter tool (Mailchimp, "
            "Laposta, Brevo, MailerLite and others). Bounces and out-of-office replies never count. Recognising a "
            "newsletter does not change its spam score. Outlook, Thunderbird and Sieve can filter on the header, "
            "for example: X-Mailfilter-Newsletter begins with yes.",
            "Een bericht telt als nieuwsbrief als het een List-Unsubscribe- of List-Id-header heeft, 'Precedence: bulk', "
            "een campagne-header van een e-mailmarketingplatform, of is verstuurd met een bekende nieuwsbrieftool "
            "(Mailchimp, Laposta, Brevo, MailerLite en andere). Bounces en afwezigheidsberichten tellen nooit mee. "
            "Herkennen verandert de spamscore niet. Outlook, Thunderbird en Sieve kunnen op de header filteren, "
            "bijvoorbeeld: X-Mailfilter-Newsletter begint met yes."),
        "options": [],
    },
    {
        "id": "newsletter_subject",
        "group": "newsletter",
        "default": False,
        "direction": "inbound",
        "symbol": None,
        "title": _t("Put a tag in the subject", "Label in het onderwerp zetten"),
        "summary": _t(
            "Puts a tag such as [Newsletter] in front of the subject of incoming newsletters.",
            "Zet een label zoals [Nieuwsbrief] voor het onderwerp van binnenkomende nieuwsbrieven."),
        "details": _t(
            "Handy for mail programs that cannot filter on headers. Changing the subject breaks the sender's DKIM "
            "signature: only switch this on when the mail server behind Mailfilter does not check DKIM or DMARC "
            "again, otherwise it may reject the newsletter.",
            "Handig voor mailprogramma's die niet op headers kunnen filteren. Het onderwerp aanpassen maakt de "
            "DKIM-handtekening van de afzender ongeldig: zet dit alleen aan als de mailserver achter Mailfilter DKIM "
            "of DMARC niet nog eens controleert, anders kan die de nieuwsbrief weigeren."),
        "options": [
            {"id": "tag", "type": "text", "default": "[Newsletter]", "max_length": 40,
             "label": _t("Tag", "Label")},
        ],
    },
    {
        "id": "newsletter_no_unsubscribe",
        "group": "newsletter",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_NEWSLETTER_NO_UNSUB",
        "title": _t("Newsletter without unsubscribe option counts as spam",
                    "Nieuwsbrief zonder afmeldmogelijkheid telt als spam"),
        "summary": _t(
            "Raises the spam score of newsletters that offer no way to unsubscribe: no List-Unsubscribe header and "
            "no unsubscribe link in the text.",
            "Verhoogt de spamscore van nieuwsbrieven zonder afmeldmogelijkheid: geen List-Unsubscribe-header en geen "
            "afmeldlink in de tekst."),
        "details": _t(
            "European law (GDPR, ePrivacy) and the rules of Gmail, Yahoo and Microsoft require every newsletter to "
            "offer an easy way to unsubscribe. A newsletter without one is almost always spam. With the default "
            "score of 4, a message that already looks a little suspicious ends up in quarantine (from score 8). "
            "Links and texts such as 'unsubscribe', 'uitschrijven', 'afmelden' and 'abmelden' count as an "
            "unsubscribe option. Optionally the rule also checks mail your own servers send out, which protects the "
            "reputation of your IP addresses.",
            "De Europese wet (AVG, ePrivacy) en de regels van Gmail, Yahoo en Microsoft eisen dat elke nieuwsbrief "
            "een makkelijke afmeldmogelijkheid heeft. Een nieuwsbrief zonder is bijna altijd spam. Met de standaard "
            "score van 4 komt een bericht dat al een beetje verdacht is in quarantaine (vanaf score 8). Links en "
            "teksten zoals 'unsubscribe', 'uitschrijven', 'afmelden' en 'abmelden' tellen als afmeldmogelijkheid. "
            "Desgewenst controleert de regel ook mail die je eigen servers versturen; dat beschermt de reputatie "
            "van je IP-adressen."),
        "options": [
            {"id": "score", "type": "score", "default": 4.0, "label": _t("Spam score", "Spamscore")},
            {"id": "outbound", "type": "bool", "default": False,
             "label": _t("Also check outgoing mail", "Ook uitgaande mail controleren")},
        ],
    },
    {
        "id": "newsletter_no_one_click",
        "group": "newsletter",
        "default": False,
        "direction": "inbound",
        "symbol": "MF_NEWSLETTER_NO_ONECLICK",
        "title": _t("Require one-click unsubscribe", "One-click afmelden verplichten"),
        "summary": _t(
            "Raises the spam score a little when a newsletter has an unsubscribe header, but not the one-click "
            "unsubscribe (RFC 8058) that Gmail and Yahoo require from bulk senders.",
            "Verhoogt de spamscore een beetje als een nieuwsbrief wel een afmeldheader heeft, maar niet het "
            "one-click afmelden (RFC 8058) dat Gmail en Yahoo van bulkverzenders eisen."),
        "details": _t(
            "One-click unsubscribe means a List-Unsubscribe header with an https link plus the header "
            "'List-Unsubscribe-Post: List-Unsubscribe=One-Click'. Large platforms all do this; small senders often "
            "do not, so this rule is off by default and has a low score.",
            "One-click afmelden betekent een List-Unsubscribe-header met een https-link plus de header "
            "'List-Unsubscribe-Post: List-Unsubscribe=One-Click'. Grote platforms doen dit allemaal, kleine "
            "verzenders vaak niet. Daarom staat deze regel standaard uit en heeft hij een lage score."),
        "options": [
            {"id": "score", "type": "score", "default": 1.0, "label": _t("Spam score", "Spamscore")},
        ],
    },
    {
        "id": "newsletter_no_dkim",
        "group": "newsletter",
        "default": True,
        "direction": "inbound",
        "symbol": "MF_NEWSLETTER_NO_DKIM",
        "title": _t("Newsletter must be DKIM-signed", "Nieuwsbrief moet met DKIM ondertekend zijn"),
        "summary": _t(
            "Raises the spam score of newsletters that are not DKIM-signed by the sender's own domain.",
            "Verhoogt de spamscore van nieuwsbrieven die niet met DKIM zijn ondertekend door het eigen domein van "
            "de afzender."),
        "details": _t(
            "Gmail and Yahoo require bulk senders to sign their mail with DKIM for the domain in the From address. "
            "Without that signature anyone could have sent the newsletter in that sender's name. A signature of a "
            "subdomain (for example news.shop.nl for shop.nl) also counts.",
            "Gmail en Yahoo eisen dat bulkverzenders hun mail met DKIM ondertekenen voor het domein in het "
            "Van-adres. Zonder die handtekening kan iedereen de nieuwsbrief uit naam van die afzender hebben "
            "gestuurd. Een handtekening van een subdomein (bijvoorbeeld nieuws.winkel.nl voor winkel.nl) telt ook."),
        "options": [
            {"id": "score", "type": "score", "default": 1.5, "label": _t("Spam score", "Spamscore")},
        ],
    },
]

from .rules_protection import PROTECTION_RULES  # noqa: E402

RULES += PROTECTION_RULES
for _r in RULES:
    _r.setdefault("per_domain", True)
BY_ID = {r["id"]: r for r in RULES}

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
_EXT_RE = re.compile(r"^[a-z0-9_-]{1,12}$")


def _list_problem(spec: dict, value: Any) -> tuple[list[str], tuple[str, str, str] | None]:
    """Normalised list + (code, en, nl) of the first problem."""
    if isinstance(value, str):
        value = re.split(r"[,;\n]+", value)
    if not isinstance(value, (list, tuple)):
        return [], ("rule_list", "Expected a list.", "Verwacht een lijst.")
    out: list[str] = []
    kind = spec.get("item", "name")
    for raw in value:
        item = " ".join(str(raw or "").split())
        if kind == "extension":
            item = item.lower().lstrip("*").lstrip(".")
        elif kind == "domain":
            item = item.lower().rstrip(".").removeprefix("@")
        if not item or item in out:
            continue
        if kind == "extension" and not _EXT_RE.match(item):
            return out, ("rule_list_item", f"'{item}' is not a file type (for example exe).",
                         f"'{item}' is geen bestandstype (bijvoorbeeld exe).")
        if kind == "domain" and not _DOMAIN_RE.match(item):
            return out, ("rule_list_item", f"'{item}' is not a domain name.", f"'{item}' is geen domeinnaam.")
        if kind == "name" and not 2 <= len(item) <= 80:
            return out, ("rule_list_item", f"'{item}': a name has 2 to 80 characters.",
                         f"'{item}': een naam heeft 2 tot 80 tekens.")
        out.append(item)
    if len(out) > spec.get("max_items", 100):
        return out, ("rule_list_long", f"At most {spec.get('max_items', 100)} entries.",
                     f"Maximaal {spec.get('max_items', 100)} regels.")
    return out, None


def default_rule(rule_id: str) -> CustomRule:
    r = BY_ID[rule_id]
    return CustomRule(enabled=r["default"], options={o["id"]: copy.deepcopy(o["default"]) for o in r["options"]})


def effective(chosen: dict[str, CustomRule]) -> dict[str, CustomRule]:
    """Every rule in the catalog, with the defaults for what was not chosen."""
    out = {}
    for r in RULES:
        base = default_rule(r["id"])
        got = chosen.get(r["id"])
        if got is not None:
            base.enabled = got.enabled
            base.options.update({k: v for k, v in got.options.items() if k in base.options})
            base.domains = dict(got.domains)
        out[r["id"]] = base
    return out


def rule_from_dict(d: dict[str, Any]) -> CustomRule:
    return CustomRule(enabled=bool(d.get("enabled", False)), options=dict(d.get("options") or {}),
                      domains={str(k): bool(v) for k, v in (d.get("domains") or {}).items()})


def problems(chosen: dict[str, CustomRule], known_domains: set[str]) -> list[tuple[str, str, str, str]]:
    """(field, code, en, nl) for everything wrong; normalises values in place."""
    out: list[tuple[str, str, str, str]] = []
    for rid, rule in chosen.items():
        if rid not in BY_ID:
            out.append((f"custom_rules.{rid}", "rule_unknown", f"Unknown rule {rid}.", f"Onbekende regel {rid}."))
            continue
        specs = {o["id"]: o for o in BY_ID[rid]["options"]}
        for key in list(rule.options):
            spec = specs.get(key)
            value = rule.options[key]
            fld = f"custom_rules.{rid}.{key}"
            if spec is None:
                del rule.options[key]
            elif spec["type"] == "score":
                try:
                    num = float(value)
                except (TypeError, ValueError):
                    num = -1.0
                if not 0 <= num <= 20:
                    out.append((fld, "rule_score", "The spam score must be a number from 0 to 20.",
                                "De spamscore moet een getal van 0 tot en met 20 zijn."))
                else:
                    rule.options[key] = round(num, 2)
            elif spec["type"] == "bool":
                rule.options[key] = bool(value)
            elif spec["type"] == "text":
                text = " ".join(str(value or "").split())
                if len(text) > spec.get("max_length", 80):
                    out.append((fld, "rule_text_long", f"At most {spec.get('max_length', 80)} characters.",
                                f"Maximaal {spec.get('max_length', 80)} tekens."))
                rule.options[key] = text
            elif spec["type"] == "choice":
                if value not in {c["id"] for c in spec["choices"]}:
                    out.append((fld, "rule_choice", "Choose one of the options.", "Kies een van de mogelijkheden."))
            elif spec["type"] == "list":
                items, problem = _list_problem(spec, value)
                if problem:
                    out.append((fld, *problem))
                rule.options[key] = items
        # Choices for a domain that was removed are dropped, so removing a domain never fails here.
        rule.domains = {d: bool(v) for d, v in ((k.strip().lower().rstrip("."), v) for k, v in rule.domains.items())
                        if d in known_domains and BY_ID[rid]["per_domain"]}
    return out


def render_arc(rules: dict[str, CustomRule]) -> dict[str, str]:
    """Rspamd's own ARC module: always checks seals; sealing and trust follow the rules."""
    trusted, out_seal, in_seal = (rules["arc_trusted_forwarders"], rules["arc_seal_outbound"],
                                  rules["arc_seal_inbound"])
    yes = lambda v: "true" if v else "false"  # noqa: E731
    lines = [
        "# Managed by Mailfilter (Configuration > Custom rules > ARC). Changes here are overwritten.",
        "# Seals use the DKIM keys: the sending domain for outgoing mail, the recipient's domain for",
        "# incoming mail. Seals of other servers are always checked.",
        "enabled = true;",
        'path = "/var/lib/rspamd/dkim/$domain.$selector.key";',
        'selector_map = "/etc/mailfilter/rspamd/dkim_selectors.map";',
        f"sign_local = {yes(out_seal.enabled)};",
        f"sign_authenticated = {yes(out_seal.enabled)};",
        f"sign_inbound = {yes(in_seal.enabled)};",
        'use_domain = "header";',
        # A forwarding server keeps the original From, so seal with its own (envelope) domain.
        'use_domain_sign_local = "envelope";',
        'use_domain_sign_inbound = "recipient";',
        "use_esld = true;",
        "allow_username_mismatch = true;",
        # Forwarded mail has someone else's From address by nature.
        "allow_hdrfrom_mismatch = true;",
        "allow_envfrom_empty = true;",
        "try_fallback = false;",
    ]
    if trusted.enabled and trusted.options.get("sealers"):
        lines += [f'whitelisted_signers_map = "{ARC_TRUSTED_MAP}";', "adjust_dmarc = true;"]
    else:
        lines += ["adjust_dmarc = false;"]
    sealers = trusted.options.get("sealers") or []
    return {
        ARC_CONF_PATH: "\n".join(lines) + "\n",
        ARC_TRUSTED_MAP: "# Managed by Mailfilter (Custom rules > ARC): trusted ARC sealers.\n"
                         + "".join(f"{d}\n" for d in sealers),
    }


def render_files(chosen: dict[str, CustomRule], active_domains: set[str]) -> dict[str, str]:
    eff = effective(chosen)
    rules = {}
    for rid, rule in eff.items():
        rules[rid] = {"enabled": rule.enabled, "options": rule.options,
                      "domains": {d: v for d, v in sorted(rule.domains.items()) if d in active_domains}}
    settings = {"_comment": "Managed by Mailfilter (Configuration > Custom rules). Changes here are overwritten.",
                "version": 1, "protected_domains": sorted(active_domains), "rules": rules}
    return {
        SETTINGS_PATH: json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        LUA_PATH: LUA_SOURCE.read_text(encoding="utf-8"),
        PROTECTION_LUA_PATH: PROTECTION_LUA_SOURCE.read_text(encoding="utf-8"),
        **render_arc(eff),
    }
