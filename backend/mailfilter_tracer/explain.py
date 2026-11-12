"""Explain a delivery problem in plain language (English and Dutch) using catalog/errors.json."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "errors.json"


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    data = json.loads(Path(path or CATALOG).read_text(encoding="utf-8"))
    for rule in data["rules"]:
        rule["_rx"] = re.compile(rule["match"], re.IGNORECASE) if rule["match"] else None
    return data


def explain_remote(dsn: str, reply: str, catalog: str | None = None) -> str | None:
    """Return the id of the first rule that fits a remote server's reply, or None for success."""
    if not dsn or dsn.startswith("2."):
        return None
    for rule in load(catalog)["rules"]:
        if not dsn.startswith(rule["dsn"]):
            continue
        if rule["_rx"] is None or rule["_rx"].search(reply or ""):
            return rule["id"]
    return None


def explain_local(reply: str, dsn: str = "") -> str:
    """Classify why Mailfilter itself refused a message before accepting it."""
    r = (reply or "").lower()
    if "blocked using" in r or "service unavailable; client" in r:
        return "rejected_blocklist"
    if "recipient's block list" in r:
        return "rejected_sender_blocked"
    if "relay access denied" in r:
        return "rejected_relay"
    if "recipient address does not exist" in r or "user unknown" in r or dsn == "5.1.1":
        return "rejected_unknown_user"
    if "spam" in r or "virus" in r or dsn.startswith("5.7.1") and "message" in r:
        return "rejected_spam"
    return "rejected_other"


def text(explain_id: str, lang: str = "en", catalog: str | None = None) -> dict | None:
    """Title and action for an explanation id, in 'en' or 'nl'."""
    data = load(catalog)
    if explain_id in data["local"]:
        return data["local"][explain_id].get(lang) or data["local"][explain_id]["en"]
    for rule in data["rules"]:
        if rule["id"] == explain_id:
            return {**(rule.get(lang) or rule["en"]), "who": rule["who"]}
    return None
