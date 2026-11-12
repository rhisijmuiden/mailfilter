"""Turn the PMG rule database into welcome/blocklists and a rule overview.

Object type numbers come from ``PMG/RuleDB/*.pm`` (``sub otype``).
"""

from __future__ import annotations

from collections import defaultdict

from .model import ImportPlan, ListEntry, RuleSummary
from .parsers import decode_bytea

# "who" objects: who sent it or who receives it
WHO_TYPES = {
    1000: "regex",
    1001: "email",
    1002: "domain",
    1003: "ip",
    1004: "network",
    1005: "ldap",
    1006: "ldap_user",
    1007: "receiver",
    1008: "receiver_domain",
    1009: "receiver_regex",
}

OTHER_TYPES = {
    2000: "Time frame",
    3000: "Spam filter",
    3001: "Virus filter",
    3002: "Match field",
    3003: "Content type filter",
    3004: "Match filename",
    3005: "Archive filter",
    3006: "Match archive filename",
    4000: "Accept",
    4001: "Block",
    4002: "Notify",
    4003: "Header attribute",
    4005: "BCC",
    4006: "Quarantine",
    4007: "Remove attachments",
    4009: "Disclaimer",
}

DIRECTIONS = {0: "in", 1: "out", 2: "both"}
GROUP_TYPES = {0: "from", 1: "to", 2: "when", 3: "what", 4: "action"}

# Group names PMG creates itself. PMG 8 used White/Blacklist, PMG 9 renamed them.
WELCOME_GROUPS = {"welcomelist", "whitelist"}
BLOCK_GROUPS = {"blocklist", "blacklist"}

# Rules PMG installs by default, and what Mailfilter does instead.
_BLOCK = ("Imported as the global blocklist", "Wordt de algemene blocklist")
_WELCOME = ("Imported as the global welcomelist", "Wordt de algemene allowlist")
_VIRUS = ("Built in: Rspamd + ClamAV reject viruses", "Standaard in Mailfilter: Rspamd + ClamAV weigeren virussen")
_HEADER = ("Built in: Rspamd adds spam headers", "Standaard in Mailfilter: Rspamd voegt spamheaders toe")
_SPAM = ("Built in: spam score thresholds (Settings > Spam)", "Standaard in Mailfilter: spamscore-drempels (Instellingen > Spam)")
_FILES = ("Built in: Rspamd blocks dangerous attachments", "Standaard in Mailfilter: Rspamd blokkeert gevaarlijke bijlagen")
DEFAULT_RULES = {
    "blocklist": _BLOCK,
    "blacklist": _BLOCK,
    "welcomelist": _WELCOME,
    "whitelist": _WELCOME,
    "virus alert": _VIRUS,
    "block viruses": _VIRUS,
    "modify header": _HEADER,
    "quarantine/mark spam (level 3)": _SPAM,
    "quarantine/mark spam (level 5)": _SPAM,
    "block spam (level 10)": _SPAM,
    "mark spam": _SPAM,
    "quarantine/mark spam (level 3) and virus": _SPAM,
    "block dangerous files": _FILES,
    "quarantine dangerous files": _FILES,
    "quarantine executable files": _FILES,
}

# PMG's example objects, never real data
PLACEHOLDERS = {"nomail@fromthisdomain.example", "mail@fromthisdomain.example"}


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _describe(otype: int, value: str | None) -> str:
    if otype in WHO_TYPES:
        return f"{WHO_TYPES[otype]}: {value}"
    name = OTHER_TYPES.get(otype, f"type {otype}")
    if otype == 3000 and value:
        return f"{name} (level {value})"
    return f"{name}: {value}" if value else name


def apply_ruledb(plan: ImportPlan, tables: dict[str, list[dict]]) -> None:
    groups = {g["id"]: g for g in tables.get("objectgroup", [])}
    objects_by_group: dict[str, list[dict]] = defaultdict(list)
    for obj in tables.get("object", []):
        obj = dict(obj)
        obj["text"] = decode_bytea(obj.get("value"))
        objects_by_group[obj.get("objectgroup_id")].append(obj)

    group_attrs: dict[str, dict[str, str | None]] = defaultdict(dict)
    for attr in tables.get("objectgroup_attributes", []):
        group_attrs[attr.get("objectgroup_id")][attr.get("name")] = decode_bytea(attr.get("value"))

    def entries_from(group_id: str, list_name: str, scope: str) -> None:
        group = groups[group_id]
        attrs = group_attrs.get(group_id, {})
        if attrs.get("invert") == "1":
            plan.warn(
                "inverted_group",
                f"Object group '{group['name']}' is inverted ('does not match'); "
                "its entries were not imported. Please check this manually.",
                f"Objectgroep '{group['name']}' is omgekeerd ('komt niet overeen'); "
                "de items zijn niet overgenomen. Loop dit met de hand na.",
            )
            return
        for obj in objects_by_group.get(group_id, []):
            otype = _int(obj.get("objecttype"))
            value = (obj.get("text") or "").strip()
            if otype not in WHO_TYPES or not value or value in PLACEHOLDERS:
                continue
            kind = WHO_TYPES[otype]
            if kind in ("ldap", "ldap_user"):
                plan.warn(
                    "ldap_object",
                    f"'{group['name']}' contains an LDAP object ({value}); Mailfilter has no LDAP, so it was skipped.",
                    f"'{group['name']}' bevat een LDAP-object ({value}); Mailfilter heeft geen LDAP, dus dit is overgeslagen.",
                )
                continue
            plan.list_entries.append(ListEntry(kind=kind, value=value, list=list_name, scope=scope, source=group["name"]))

    # Global welcome/blocklists (who groups) and the SMTP welcomelist (class greylist)
    for gid, group in groups.items():
        name = (group.get("name") or "").strip().lower()
        cls = (group.get("class") or "").lower()
        if cls == "who" and name in WELCOME_GROUPS:
            entries_from(gid, "welcome", "global")
        elif cls == "who" and name in BLOCK_GROUPS:
            entries_from(gid, "block", "global")
        elif cls == "greylist":
            entries_from(gid, "welcome", "smtp")

    # Per-user lists from the quarantine interface
    for pref in tables.get("userprefs", []):
        list_name = {"WL": "welcome", "BL": "block"}.get(pref.get("name") or "")
        owner = pref.get("pmail") or ""
        if not list_name or not owner:
            continue
        for addr in (pref.get("data") or "").replace(",", " ").replace(";", " ").split():
            kind = "email" if "@" in addr and not addr.startswith("@") else "domain"
            value = addr.lstrip("@") if kind == "domain" else addr
            plan.list_entries.append(ListEntry(kind=kind, value=value, list=list_name, scope=f"user:{owner}", source="user"))

    # Rules overview
    rule_groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for rg in tables.get("rulegroup", []):
        rule_groups[rg.get("rule_id")].append((_int(rg.get("grouptype"), -1), rg.get("objectgroup_id")))

    for rule in tables.get("rule", []):
        name = rule.get("name") or f"Rule {rule.get('id')}"
        summary = RuleSummary(
            name=name,
            priority=_int(rule.get("priority")),
            active=_int(rule.get("active")) == 1,
            direction=DIRECTIONS.get(_int(rule.get("direction"), 2), "both"),
        )
        for gtype, gid in sorted(rule_groups.get(rule.get("id"), [])):
            group = groups.get(gid)
            if not group:
                continue
            if gtype == 4:
                for obj in objects_by_group.get(gid, []):
                    summary.actions.append(_describe(_int(obj.get("objecttype")), None))
                continue
            objs = objects_by_group.get(gid, [])
            detail = ", ".join(_describe(_int(o.get("objecttype")), o.get("text")) for o in objs[:5])
            if len(objs) > 5:
                detail += f", +{len(objs) - 5} more"
            label = f"{group['name']} ({detail})" if detail else group["name"]
            target = {0: summary.from_groups, 1: summary.to_groups, 2: summary.when_groups, 3: summary.what_groups}.get(gtype)
            if target is not None:
                target.append(label)
        key = name.strip().lower()
        if key in DEFAULT_RULES:
            summary.is_default = True
            summary.handled_by, summary.handled_by_nl = DEFAULT_RULES[key]
        elif not summary.active:
            summary.handled_by, summary.handled_by_nl = "Not active in PMG, skipped", "Stond uit in PMG, overgeslagen"
        plan.rules.append(summary)

    plan.rules.sort(key=lambda r: -r.priority)
    custom = [r.name for r in plan.rules if not r.handled_by]
    if custom:
        plan.warn(
            "custom_rules",
            "These PMG rules have no automatic counterpart and need a manual check: " + ", ".join(custom),
            "Deze PMG-regels hebben geen automatische tegenhanger en moet je met de hand nalopen: " + ", ".join(custom),
        )
