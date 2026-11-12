"""Allow and block lists: checking what people type, and writing the Rspamd maps.

Every entry becomes one line ``<recipient>|<sender>`` in ``allow.map`` or
``block.map`` under ``Settings.lists_dir``. Rspamd's multimap rules in
install/templates/rspamd/local.d/multimap.conf look up every combination of
(recipient address, recipient domain, "*") with (sender address, sender
domain), using the envelope addresses. Rspamd notices changed map files by
itself, usually within a minute. An allowed sender gets -20 points (a virus or
blocked attachment is still quarantined); a blocked sender is refused.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SenderListEntry
from .settings import get_settings

LISTS = ("allow", "block")
EVERYONE = "*"
MAP_FILES = {"allow": "allow.map", "block": "block.map"}

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_LOCAL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{}~.-]{1,64}$")


class ListError(ValueError):
    """Bad input; ``code`` is translated by the web interface."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def norm_domain(value: str) -> str:
    d = value.strip().lower().rstrip(".")
    try:
        d = d.encode("idna").decode("ascii") if d and not d.isascii() else d
    except UnicodeError as e:
        raise ListError("bad_domain") from e
    labels = d.split(".")
    if len(d) > 253 or len(labels) < 2 or not all(_LABEL.match(x) for x in labels) or labels[-1].isdigit():
        raise ListError("bad_domain")
    return d


def norm_address(value: str) -> str:
    local, sep, domain = value.strip().lower().rpartition("@")
    if not sep or not _LOCAL.match(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        raise ListError("bad_address")
    return f"{local}@{norm_domain(domain)}"


def norm_sender(value: str) -> str:
    """An address, or a domain (an "@" in front is allowed: "@shop.example")."""
    v = value.strip().lower()
    if not v:
        raise ListError("bad_sender")
    if v.startswith("@"):
        v = v[1:]
    if "*" in v:
        raise ListError("no_wildcards")
    try:
        return norm_address(v) if "@" in v else norm_domain(v)
    except ListError as e:
        raise ListError("bad_sender") from e


def norm_recipient(value: str) -> str:
    v = value.strip().lower()
    if v == EVERYONE:
        return v
    if v.startswith("@"):
        v = v[1:]
    try:
        return norm_address(v) if "@" in v else norm_domain(v)
    except ListError as e:
        raise ListError("bad_recipient") from e


def recipient_domain(recipient: str) -> str | None:
    if recipient == EVERYONE:
        return None
    return recipient.rsplit("@", 1)[-1]


def map_lines(entries: list[SenderListEntry]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {name: [] for name in LISTS}
    for e in entries:
        out[e.list].append(f"{e.recipient}|{e.sender}")
    return {k: sorted(set(v)) for k, v in out.items()}


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)  # Rspamd runs as its own user and must read it
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_maps(db: Session) -> dict[str, int]:
    """Write allow.map and block.map from the database; returns lines per list."""
    entries = db.execute(select(SenderListEntry)).scalars().all()
    lines = map_lines(list(entries))
    target = get_settings().lists_dir
    header = "# Managed by Mailfilter (Allow and block lists). Changes here are overwritten.\n"
    for name, rows in lines.items():
        _write_atomic(target / MAP_FILES[name], header + "".join(f"{r}\n" for r in rows))
    return {k: len(v) for k, v in lines.items()}
