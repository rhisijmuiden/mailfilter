"""Plain-language explanations (EN/NL) for delivery problems, from catalog/errors.json
(shared with the log collector, which stores the explanation id per recipient)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .settings import get_settings


def _catalog_path() -> Path:
    installed = get_settings().source_dir / "catalog" / "errors.json"
    if installed.exists():
        return installed
    return Path(__file__).resolve().parents[2] / "catalog" / "errors.json"


@lru_cache
def catalog() -> dict:
    return json.loads(_catalog_path().read_text(encoding="utf-8"))


def text(explain_id: str | None, lang: str) -> dict | None:
    """{"title", "action", "who"} for an explanation id, or None."""
    if not explain_id:
        return None
    data = catalog()
    if explain_id in data["local"]:
        entry = data["local"][explain_id]
        return {**(entry.get(lang) or entry["en"]), "who": "us" if explain_id != "rejected_unknown_user" else "sender"}
    for rule in data["rules"]:
        if rule["id"] == explain_id:
            return {**(rule.get(lang) or rule["en"]), "who": rule["who"]}
    return None


_SMTP_PREFIX = re.compile(r"^\s*(host \S+?\s?\[[^\]]*\] said:\s*)?", re.I)
_SMTP_SUFFIX = re.compile(r"\s*\(in reply to [^)]*\)\s*$", re.I)


def short_reply(reply: str | None) -> str:
    """The remote server's own words, without Postfix's "host x said:" wrapper."""
    return _SMTP_SUFFIX.sub("", _SMTP_PREFIX.sub("", reply or "")).strip()
