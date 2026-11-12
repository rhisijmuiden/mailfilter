"""DKIM keys for a cluster: every node signs with the same key per domain.

`mailfilter-config dkim --stdin --json` reads one JSON request:

  {"action": "export"}
      -> {"ok": true, "keys": [{"domain", "selector", "key", "txt"}, ...]}
  {"action": "import", "keys": [...]}
      -> {"ok": true, "changed": ["example.nl", ...]}

Import writes the key under /var/lib/rspamd/dkim (owner _rspamd, 0640), the
DNS record next to the others in /etc/mailfilter and points the domain's
entry in dkim_selectors.map at the selector. Keys of domains that are not in
the request are left alone. The web app runs this as root through sudo, the
same way as `apply`.
"""
from __future__ import annotations

import re
from typing import Any

from .apply import DKIM_DIR, DKIM_MAP, Applier, _chown

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
SELECTOR_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
KEY_RE = re.compile(r"^-----BEGIN (?:RSA )?PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+-----END (?:RSA )?PRIVATE KEY-----\n?$")
MAX_KEYS = 5000


def txt_path(domain: str, selector: str) -> str:
    return f"/etc/mailfilter/dkim-{domain}.{selector}.txt"


def key_path(domain: str, selector: str) -> str:
    return f"{DKIM_DIR}/{domain}.{selector}.key"


def _read_map(ap: Applier) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in (ap.read(DKIM_MAP) or "").splitlines():
        parts = ln.split()
        if len(parts) >= 2 and not ln.lstrip().startswith("#"):
            out[parts[0].lower()] = parts[1]
    return out


def export_keys(ap: Applier) -> list[dict[str, str]]:
    keys = []
    for domain, selector in sorted(_read_map(ap).items()):
        key = ap.read(key_path(domain, selector))
        if key is None:
            continue
        keys.append({"domain": domain, "selector": selector, "key": key,
                     "txt": ap.read(txt_path(domain, selector)) or ""})
    return keys


def check_key(item: Any) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ValueError("each key must be an object")
    domain = str(item.get("domain", "")).strip().lower().rstrip(".")
    selector = str(item.get("selector", "")).strip().lower()
    key = str(item.get("key", ""))
    txt = str(item.get("txt", ""))
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"not a domain name: {domain!r}")
    if not SELECTOR_RE.match(selector):
        raise ValueError(f"not a DKIM selector: {selector!r}")
    if not KEY_RE.match(key.replace("\r\n", "\n")):
        raise ValueError(f"DKIM key for {domain} is not a PEM private key")
    if len(txt) > 8000 or "\x00" in txt:
        raise ValueError(f"DKIM record for {domain} is not valid")
    return {"domain": domain, "selector": selector, "key": key.replace("\r\n", "\n"), "txt": txt}


def import_keys(ap: Applier, items: list[Any]) -> list[str]:
    if not isinstance(items, list) or len(items) > MAX_KEYS:
        raise ValueError("keys must be a list")
    keys = [check_key(i) for i in items]
    changed: list[str] = []
    with ap.lock():
        current = _read_map(ap)
        new_map = dict(current)
        for k in keys:
            d, s = k["domain"], k["selector"]
            kp, tp = key_path(d, s), txt_path(d, s)
            if ap.read(kp) != k["key"]:
                ap.write(kp, k["key"])
                _chown(ap.path(kp), "_rspamd", "_rspamd", 0o640)
                changed.append(d)
            if k["txt"] and ap.read(tp) != k["txt"]:
                ap.write(tp, k["txt"])
                _chown(ap.path(tp), "root", "mailfilter", 0o640)
            if new_map.get(d) != s:
                new_map[d] = s
                if d not in changed:
                    changed.append(d)
        if new_map != current:
            # Keep comments and the order of existing lines; replace changed selectors.
            lines, seen = [], set()
            for ln in (ap.read(DKIM_MAP) or "").splitlines():
                parts = ln.split()
                if len(parts) >= 2 and not ln.lstrip().startswith("#"):
                    name = parts[0].lower()
                    seen.add(name)
                    lines.append(f"{name} {new_map[name]}")
                else:
                    lines.append(ln)
            lines += [f"{d} {s}" for d, s in new_map.items() if d not in seen]
            ap.write(DKIM_MAP, "\n".join(lines) + "\n")
    return sorted(changed)


def handle(request: Any, ap: Applier | None = None) -> dict[str, Any]:
    ap = ap or Applier()
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    action = request.get("action")
    if action == "export":
        return {"ok": True, "keys": export_keys(ap)}
    if action == "import":
        return {"ok": True, "changed": import_keys(ap, request.get("keys", []))}
    raise ValueError(f"unknown action: {action!r}")
