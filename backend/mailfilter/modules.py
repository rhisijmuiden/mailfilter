"""Optional modules: parts of Mailfilter an admin can switch off (System > Modules).

Core filtering (spam, virus, relay, quarantine, trace, lists, domains) is always
on. A module that is off:
  * disappears from the menu and its page shows "this module is off";
  * its API answers 403 module_disabled (see ``require``);
  * its background work stops (timers skip it, mail pipes drop what arrives);
  * when it changes the mail flow (``gateway``), the gateway is re-applied
    without it.
Data a module stored stays, so switching it back on continues where it was.

The switches live in the setting ``modules`` ({key: bool}), synced in a
cluster. The Mailtrap keeps its own per-node switch ``mailtrap.enabled`` from
before this page existed.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .settings_store import get_setting, set_setting

SETTING_KEY = "modules"


@dataclass(frozen=True)
class Module:
    key: str
    default: bool = True
    gateway: bool = False  # switching re-applies the mail gateway
    setting: str | None = None  # own setting key instead of SETTING_KEY[key]
    per_node: bool = False  # not synced in a cluster


MODULES: tuple[Module, ...] = (
    Module("usage"),             # Usage & billing (+ the reseller role's only page)
    Module("monthly_report"),    # monthly report page + mail
    Module("quarantine_report"), # daily quarantine report mail
    Module("spam_reports"),      # spam@ / notspam@ report addresses
    Module("dmarc"),             # DMARC aggregate reports (rua)
    Module("link_protection", gateway=True),
    Module("archive", gateway=True),
    Module("mailtrap", default=False, gateway=True, setting="mailtrap.enabled", per_node=True),
    Module("cloud", gateway=True),  # Microsoft 365 / Google Workspace wizard and relay
    Module("api"),               # REST API with API keys
    Module("cluster"),
    Module("pmg_import"),
)
BY_KEY = {m.key: m for m in MODULES}


def states(db: Session) -> dict[str, bool]:
    stored = get_setting(db, SETTING_KEY) or {}
    out = {}
    for m in MODULES:
        value = get_setting(db, m.setting) if m.setting else stored.get(m.key)
        out[m.key] = m.default if value is None else bool(value)
    return out


def enabled(db: Session, key: str) -> bool:
    m = BY_KEY[key]
    value = get_setting(db, m.setting) if m.setting else (get_setting(db, SETTING_KEY) or {}).get(key)
    return m.default if value is None else bool(value)


def set_enabled(db: Session, key: str, on: bool) -> None:
    m = BY_KEY[key]
    if m.setting:
        set_setting(db, m.setting, on)
        return
    stored = dict(get_setting(db, SETTING_KEY) or {})
    stored[key] = on
    set_setting(db, SETTING_KEY, stored)


def check(db: Session, key: str) -> None:
    if not enabled(db, key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "module_disabled", "module": key})


def require(key: str):
    """Router/endpoint dependency: 403 module_disabled while the module is off."""
    BY_KEY[key]  # fail at import time on a typo

    def dep(db: Session = Depends(get_db)) -> None:
        check(db, key)

    return Depends(dep)
