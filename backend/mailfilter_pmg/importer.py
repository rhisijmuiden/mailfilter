"""Take an :class:`ImportPlan` over into Mailfilter.

Two steps, both also used by the web interface:

* :func:`preview` lists every importable item with a status (new, already
  there, cannot be imported) and whether it is ticked by default, and runs the
  gateway validation on the result so problems show up before anything changes.
* :func:`merge_gateway` adds the ticked domains, routes, relay networks and
  TLS policies to a gateway configuration and validates it; the web app saves
  that in its database and applies it through ``mailfilter_config``.
* :func:`apply_server_files` writes the ticked allow/block list entries and
  DKIM keys into the Rspamd maps (root only), which Rspamd reloads on its own.

The rule is simple: a ticked item takes PMG's version. Items that already
exist in Mailfilter are not ticked by default, so nothing is overwritten
unless the admin asks for it.
"""

from __future__ import annotations

import grp
import ipaddress
import os
import pwd
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

from mailfilter_config.apply import STATE_FILE, Applier, load_state
from mailfilter_config.model import ALL_TLS_MODES, AddressRoute, Destination, Domain, GatewayConfig, RelayNetwork, TlsPolicy
from mailfilter_config.validate import validate

from .model import ImportPlan, ListEntry, Transport

SECTIONS = ("domains", "address_routes", "relay_networks", "tls_policies", "lists", "dkim")

RSPAMD_MAPS = "/etc/mailfilter/rspamd"
DKIM_DIR = "/var/lib/rspamd/dkim"
DKIM_SELECTORS = f"{RSPAMD_MAPS}/dkim_selectors.map"

# (list, kind) -> Rspamd multimap file (see install/templates/rspamd/local.d/multimap.conf)
LIST_MAPS = {
    ("welcome", "email"): "allow_from.map",
    ("welcome", "domain"): "allow_domain.map",
    ("welcome", "ip"): "allow_ip.map",
    ("welcome", "network"): "allow_ip.map",
    ("welcome", "regex"): "allow_from_re.map",
    ("block", "email"): "block_from.map",
    ("block", "domain"): "block_domain.map",
    ("block", "ip"): "block_ip.map",
    ("block", "network"): "block_ip.map",
    ("block", "regex"): "block_from_re.map",
}
MAP_HEADER = "# Imported from Proxmox Mail Gateway\n"


@dataclass
class Item:
    section: str
    key: str
    label: str
    detail: str = ""
    status: str = "new"  # new, exists, unsupported
    selected: bool = True  # ticked by default
    note_en: str = ""
    note_nl: str = ""
    problems: list[dict] = field(default_factory=list)


@dataclass
class Preview:
    items: list[Item]
    problems: list[dict]  # validation problems not tied to one item

    def to_dict(self) -> dict[str, Any]:
        return {"items": [asdict(i) for i in self.items], "problems": self.problems}


@dataclass
class ImportResult:
    ok: bool
    imported: dict[str, int] = field(default_factory=dict)
    problems: list[dict] = field(default_factory=list)
    error: str = ""
    changed_files: list[str] = field(default_factory=list)


# ---- conversion -------------------------------------------------------------------------


def net_key(cidr: str) -> str:
    """Canonical network, so 192.0.2.10, 192.0.2.10/32 and 2001:DB8::/64 compare equal."""
    try:
        return str(ipaddress.ip_network(cidr.strip(), strict=False))
    except ValueError:
        return cidr.strip().lower()


def _dest(t: Transport) -> Destination:
    return Destination(host=t.host, port=t.port, mx=t.use_mx, protocol=t.protocol)


def _dest_text(t: Transport | None) -> str:
    if t is None:
        return ""
    host = t.host if t.use_mx else f"[{t.host}]"
    return f"{t.protocol}:{host}:{t.port}"


def list_key(e: ListEntry) -> str:
    return f"{e.list}:{e.scope}:{e.kind}:{e.value}"


def map_line(e: ListEntry) -> str:
    if e.kind == "regex":
        # PMG matches the whole address, case-insensitive
        return f"/^(?:{e.value})$/i"
    return e.value.lower() if e.kind in ("email", "domain") else e.value


def _plan_items(plan: ImportPlan) -> dict[str, list[tuple[str, Any]]]:
    """Every importable thing in the plan, keyed per section."""
    out: dict[str, list[tuple[str, Any]]] = {s: [] for s in SECTIONS}
    for d in plan.domains:
        out["domains"].append((d.name, d))
    for a in plan.address_transports:
        out["address_routes"].append((a.match, a))
    for n in plan.relay_networks:
        out["relay_networks"].append((n.cidr, n))
    for t in plan.tls_policies:
        out["tls_policies"].append((t.destination, t))
    seen = set()
    for e in plan.list_entries:
        k = list_key(e)
        if k not in seen:
            seen.add(k)
            out["lists"].append((k, e))
    if plan.dkim and plan.dkim.private_key_pem:
        for name in plan.dkim.domains:
            out["dkim"].append((name, plan.dkim))
    return out


class MapStore:
    """Reads and writes the Rspamd map files and DKIM keys (below ``root`` in tests)."""

    def __init__(self, root: str = ""):
        self.root = root.rstrip("/")

    def path(self, p: str) -> str:
        return self.root + p

    def read(self, p: str) -> str:
        try:
            with open(self.path(p), encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def lines(self, p: str) -> set[str]:
        return {ln.strip() for ln in self.read(p).splitlines() if ln.strip() and not ln.lstrip().startswith("#")}

    def dkim_selectors(self) -> dict[str, str]:
        out = {}
        for ln in self.lines(DKIM_SELECTORS):
            parts = ln.split()
            if len(parts) >= 2:
                out[parts[0].lower()] = parts[1]
        return out

    def write(self, p: str, content: str, mode: int = 0o644, owner: tuple[str, str] | None = None) -> None:
        dest = self.path(p)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        uid = gid = -1
        try:
            st = os.stat(dest)
            mode, uid, gid = st.st_mode & 0o7777, st.st_uid, st.st_gid
        except FileNotFoundError:
            if owner and os.geteuid() == 0:
                try:
                    uid, gid = pwd.getpwnam(owner[0]).pw_uid, grp.getgrnam(owner[1]).gr_gid
                except KeyError:
                    pass
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".mfpmg-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.chmod(tmp, mode)
            if uid != -1 and os.geteuid() == 0:
                os.chown(tmp, uid, gid)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def _build(plan: ImportPlan, current: GatewayConfig, chosen: dict[str, set[str]]) -> tuple[GatewayConfig, dict]:
    """``current`` plus the chosen items; also returns which config index belongs to which item."""
    cfg = GatewayConfig.from_dict(current.to_dict())
    index: dict[str, str] = {}  # "domains[3]" -> item key
    items = _plan_items(plan)

    for key, d in items["domains"]:
        if key not in chosen.get("domains", set()) or d.transport is None:
            continue
        new = Domain(name=d.name, destinations=[_dest(d.transport)], note=d.comment or "Imported from PMG")
        pos = next((i for i, x in enumerate(cfg.domains) if x.name.lower() == key), None)
        if pos is None:
            cfg.domains.append(new)
            pos = len(cfg.domains) - 1
        else:  # keep Mailfilter-only settings (customer, limits), take PMG's destination
            old = cfg.domains[pos]
            old.destinations, old.note = new.destinations, old.note or new.note
        index[f"domains[{pos}]"] = key

    for key, a in items["address_routes"]:
        if key not in chosen.get("address_routes", set()):
            continue
        new = AddressRoute(address=a.match, destination=_dest(a.transport), note=a.transport.comment or "Imported from PMG")
        pos = next((i for i, x in enumerate(cfg.address_routes) if x.address.lower() == key), None)
        if pos is None:
            cfg.address_routes.append(new)
            pos = len(cfg.address_routes) - 1
        else:
            cfg.address_routes[pos] = new
        index[f"address_routes[{pos}]"] = key

    for key, n in items["relay_networks"]:
        if key not in chosen.get("relay_networks", set()):
            continue
        pos = next((i for i, x in enumerate(cfg.relay_networks) if net_key(x.cidr) == net_key(key)), None)
        if pos is None:
            cfg.relay_networks.append(RelayNetwork(cidr=n.cidr, description=n.comment or "Imported from PMG"))
            pos = len(cfg.relay_networks) - 1
        else:
            cfg.relay_networks[pos].enabled = True
        index[f"relay_networks[{pos}]"] = key

    for key, t in items["tls_policies"]:
        if key not in chosen.get("tls_policies", set()):
            continue
        new = TlsPolicy(destination=t.destination, level=t.policy.split()[0], note="Imported from PMG")
        pos = next((i for i, x in enumerate(cfg.tls_policies) if x.destination.lower() == key), None)
        if pos is None:
            cfg.tls_policies.append(new)
            pos = len(cfg.tls_policies) - 1
        else:
            cfg.tls_policies[pos] = new
        index[f"tls_policies[{pos}]"] = key

    return cfg, index


def _problems_by_item(problems, index: dict[str, str]) -> tuple[dict[tuple[str, str], list[dict]], list[dict]]:
    per_item: dict[tuple[str, str], list[dict]] = {}
    general = []
    for p in problems:
        d = asdict(p)
        head = p.field.split(".", 1)[0]
        if head in index:
            per_item.setdefault((head.split("[")[0], index[head]), []).append(d)
        else:
            general.append(d)
    return per_item, general


def preview(plan: ImportPlan, current: GatewayConfig | None = None, root: str = "") -> Preview:
    current = current if current is not None else GatewayConfig()
    store = MapStore(root)
    items: list[Item] = []
    have_domains = {d.name.lower() for d in current.domains}
    have_routes = {a.address.lower() for a in current.address_routes}
    have_nets = {net_key(n.cidr) for n in current.relay_networks}
    have_tls = {t.destination.lower() for t in current.tls_policies}
    selectors = store.dkim_selectors()
    map_cache: dict[str, set[str]] = {}

    def exists(item: Item, en: str = "Already in Mailfilter; tick to take over PMG's version.",
               nl: str = "Staat al in Mailfilter; vink aan om de versie uit PMG over te nemen.") -> None:
        item.status, item.selected, item.note_en, item.note_nl = "exists", False, en, nl

    def unsupported(item: Item, en: str, nl: str) -> None:
        item.status, item.selected, item.note_en, item.note_nl = "unsupported", False, en, nl

    pi = _plan_items(plan)
    for key, d in pi["domains"]:
        it = Item("domains", key, d.name, _dest_text(d.transport))
        if d.transport is None:
            unsupported(it, "No destination server in PMG; add the domain by hand.",
                        "Geen doelserver in PMG; voeg het domein met de hand toe.")
        elif key in have_domains:
            exists(it)
        if d.require_tls_inbound:
            it.note_en = (it.note_en + " PMG required TLS from senders for this domain; Mailfilter does not enforce that yet.").strip()
            it.note_nl = (it.note_nl + " PMG eiste TLS van verzenders voor dit domein; Mailfilter dwingt dat nog niet af.").strip()
        items.append(it)
    for key, a in pi["address_routes"]:
        it = Item("address_routes", key, a.match, _dest_text(a.transport))
        if key in have_routes:
            exists(it)
        items.append(it)
    for key, n in pi["relay_networks"]:
        it = Item("relay_networks", key, n.cidr, n.comment)
        if net_key(key) in have_nets:
            exists(it, "Already allowed to relay.", "Mag al relayen.")
        items.append(it)
    for key, t in pi["tls_policies"]:
        it = Item("tls_policies", key, t.destination, t.policy)
        if t.policy.split()[0] not in ALL_TLS_MODES:
            unsupported(it, f"TLS level '{t.policy}' is not supported; set it by hand.",
                        f"TLS-niveau '{t.policy}' wordt niet ondersteund; stel het met de hand in.")
        elif key in have_tls:
            exists(it)
        items.append(it)
    for key, e in pi["lists"]:
        label = f"{e.value}"
        detail = {"welcome": "welcome", "block": "block"}[e.list] + f" · {e.kind}" + ("" if e.scope == "global" else f" · {e.scope}")
        it = Item("lists", key, label, detail)
        target = LIST_MAPS.get((e.list, e.kind))
        if e.scope.startswith("user:"):
            unsupported(it, "Personal lists of quarantine users are not supported yet.",
                        "Persoonlijke lijsten van quarantainegebruikers worden nog niet ondersteund.")
        elif target is None:
            unsupported(it, "This kind of entry (recipient match) has no counterpart in Mailfilter yet.",
                        "Dit soort regel (op ontvanger) heeft nog geen tegenhanger in Mailfilter.")
        else:
            path = f"{RSPAMD_MAPS}/{target}"
            if path not in map_cache:
                map_cache[path] = store.lines(path)
            if map_line(e) in map_cache[path]:
                exists(it, "Already on the list.", "Staat al op de lijst.")
                it.selected = False
            elif e.scope == "smtp":
                it.note_en = "PMG SMTP welcomelist; imported as a normal welcomelist entry."
                it.note_nl = "SMTP-welcomelist uit PMG; wordt een gewone welcomelist-regel."
        items.append(it)
    for key, dk in pi["dkim"]:
        it = Item("dkim", key, key, f"selector {dk.selector}")
        if selectors.get(key) == dk.selector:
            exists(it, "This selector is already set up.", "Deze selector staat al ingesteld.")
        elif key in selectors:
            exists(it, f"Mailfilter already has a DKIM key for this domain (selector {selectors[key]}); "
                       "tick to use PMG's key, which is the one published in DNS now.",
                   f"Mailfilter heeft al een DKIM-sleutel voor dit domein (selector {selectors[key]}); "
                   "vink aan om de sleutel van PMG te gebruiken, die nu in DNS staat.")
        items.append(it)

    chosen = {s: {i.key for i in items if i.section == s and i.selected} for s in SECTIONS}
    cfg, index = _build(plan, current, chosen)
    per_item, general = _problems_by_item(validate(cfg), index)
    for it in items:
        it.problems = per_item.get((it.section, it.key), [])
    return Preview(items=items, problems=general)


GATEWAY_SECTIONS = ("domains", "address_routes", "relay_networks", "tls_policies")
SERVER_SECTIONS = ("lists", "dkim")


def merge_gateway(
    plan: ImportPlan, current: GatewayConfig, selection: dict[str, list[str]]
) -> tuple[GatewayConfig, dict[str, list], list[dict]]:
    """``current`` plus the selected domains, routes, networks and TLS policies.

    Returns the new (validated, normalised) configuration, per section the
    objects in it that came from PMG, and validation problems (empty when it is
    safe to save). The web app saves the picked objects into its database,
    which is the source of truth for the gateway.
    """
    chosen = {s: set(selection.get(s, [])) for s in SECTIONS}
    cfg, index = _build(plan, current, chosen)
    problems = [asdict(p) for p in validate(cfg)]  # also normalises names and networks in place
    picked: dict[str, list] = {s: [] for s in GATEWAY_SECTIONS}
    for ref in index:
        section, pos = ref.rstrip("]").split("[")
        picked[section].append(getattr(cfg, section)[int(pos)])
    return cfg, picked, problems


def apply_gateway_file(
    plan: ImportPlan, selection: dict[str, list[str]], root: str = "",
    applier: Applier | None = None, state_file: str = STATE_FILE,
) -> ImportResult:
    """Merge into /etc/mailfilter/gateway.json and apply it (command line, before the web app runs)."""
    applier = applier or Applier(root=root)
    current = load_state(MapStore(root).path(state_file))
    cfg, picked, _ = merge_gateway(plan, current, selection)
    counts = {k: len(v) for k, v in picked.items()}
    applier.fill_server_facts(cfg)
    problems = validate(cfg)
    if problems:
        return ImportResult(ok=False, problems=[asdict(p) for p in problems])
    report = applier.apply(cfg)  # raises ApplyError after a full rollback
    return ImportResult(ok=True, imported=counts, changed_files=list(report.changed))


def apply_server_files(plan: ImportPlan, selection: dict[str, list[str]], root: str = "") -> ImportResult:
    """Write the selected allow/block list entries and DKIM keys (needs root)."""
    chosen = {s: set(selection.get(s, [])) for s in SECTIONS}
    store = MapStore(root)
    result = ImportResult(ok=True)

    # Allow and block lists
    additions: dict[str, list[str]] = {}
    count = 0
    for key, e in _plan_items(plan)["lists"]:
        target = LIST_MAPS.get((e.list, e.kind))
        if key not in chosen["lists"] or target is None or e.scope.startswith("user:"):
            continue
        additions.setdefault(f"{RSPAMD_MAPS}/{target}", []).append(map_line(e))
    for path, new_lines in additions.items():
        have = store.lines(path)
        todo = [ln for ln in dict.fromkeys(new_lines) if ln not in have]
        if not todo:
            continue
        text = store.read(path)
        if text and not text.endswith("\n"):
            text += "\n"
        if MAP_HEADER not in text:
            text += MAP_HEADER
        store.write(path, text + "".join(ln + "\n" for ln in todo))
        result.changed_files.append(path)
        count += len(todo)
    result.imported["lists"] = count

    # DKIM: PMG uses one key for all domains; Rspamd wants one file per domain
    dk = plan.dkim
    dkim_domains = [d for d in (dk.domains if dk else []) if d in chosen["dkim"]]
    if dk and dk.private_key_pem and dkim_domains:
        for name in dkim_domains:
            p = f"{DKIM_DIR}/{name}.{dk.selector}.key"
            store.write(p, dk.private_key_pem, mode=0o640, owner=("_rspamd", "_rspamd"))
            result.changed_files.append(p)
        selectors = store.dkim_selectors()
        selectors.update({name: dk.selector for name in dkim_domains})
        store.write(DKIM_SELECTORS, "".join(f"{d} {s}\n" for d, s in sorted(selectors.items())))
        result.changed_files.append(DKIM_SELECTORS)
    result.imported["dkim"] = len(dkim_domains) if dk and dk.private_key_pem else 0
    return result

