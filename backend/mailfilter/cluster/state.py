"""Cluster state on this node, the shared secret and the sealed messages nodes exchange.

Nodes talk HTTPS (port 443, the normal web interface certificate, verified).
Every request and answer body is additionally a Fernet token made with a key
derived from the cluster secret: encrypted (AES) and authenticated (HMAC), so
only nodes that know the secret can read or forge it. A token says what it is
for, carries a random nonce (each request is accepted once) and a timestamp
(older than five minutes is refused), and an answer names the request nonce
it belongs to.

Nothing here is active until the admin creates or joins a cluster: a single
server has no "cluster" setting and behaves exactly as before.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ClusterNode, ClusterNonce
from ..settings import get_settings
from ..settings_store import get_secret, get_setting, set_secret, set_setting

CONFIG_KEY = "cluster"  # {"cluster_id", "node_id", "spf_name", "created_at"}
SECRET_KEY = "cluster.secret"
PREVIOUS_SECRET_KEY = "cluster.secret_previous"  # {"secret": enc, "until": iso}; only used for sending
REVISION_KEY = "cluster.revision"  # {"at": iso, "node": node_id}
LAST_IMPORT_KEY = "cluster.last_import"

MAX_AGE = 300  # seconds a sealed message stays valid
JOIN_PREFIX = "MFC1."
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


class ClusterError(Exception):
    """code is shown to the admin (translated in the UI)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---- this node -----------------------------------------------------------------
def config(db: Session) -> dict | None:
    return get_setting(db, CONFIG_KEY) or None


def enabled(db: Session) -> bool:
    return bool(config(db))


def secret(db: Session) -> str:
    s = get_secret(db, SECRET_KEY)
    if not s:
        raise ClusterError("not_in_cluster")
    return s


def previous_secret(db: Session) -> str | None:
    prev = get_setting(db, PREVIOUS_SECRET_KEY)
    if not prev or prev.get("until", "") < now().isoformat():
        return None
    return get_secret(db, PREVIOUS_SECRET_KEY + ".value")


def set_cluster_secret(db: Session, new: str) -> None:
    """Store a new secret; the old one stays usable for *sending* for a day so
    nodes that missed the change still get it with the next push."""
    old = get_secret(db, SECRET_KEY)
    if old and old != new:
        set_secret(db, PREVIOUS_SECRET_KEY + ".value", old)
        set_setting(db, PREVIOUS_SECRET_KEY, {"until": (now() + timedelta(days=1)).isoformat()})
    set_secret(db, SECRET_KEY, new)


def new_id() -> str:
    return secrets.token_hex(8)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def default_name() -> str:
    host = get_settings().hostname
    first = host.split(".")[0] if host and host != "localhost" else "mailfilter"
    return (first if first[-1:].isdigit() else f"{first}01")[:63]


def own_url() -> str:
    host = get_settings().hostname
    return f"https://{host}" if host and host != "localhost" else ""


def own_addresses() -> list[str]:
    s = get_settings()
    return [a for a in (s.public_v4, s.public_v6) if a]


def self_node(db: Session) -> ClusterNode | None:
    return db.execute(select(ClusterNode).where(ClusterNode.is_self.is_(True))).scalar_one_or_none()


def peers(db: Session) -> list[ClusterNode]:
    return list(db.execute(select(ClusterNode).where(ClusterNode.is_self.is_(False)).order_by(ClusterNode.name)).scalars())


def node_info(n: ClusterNode) -> dict[str, Any]:
    return {"node_id": n.node_id, "name": n.name, "url": n.url, "addresses": list(n.addresses or [])}


def cluster_server_facts(db: Session) -> dict[str, Any] | None:
    """SPF name and the public addresses of every node, or None outside a cluster."""
    import ipaddress

    cfg = config(db)
    if not cfg:
        return None
    v4: list[str] = []
    v6: list[str] = []
    for n in db.execute(select(ClusterNode).order_by(ClusterNode.name)).scalars():
        for a in n.addresses or []:
            try:
                (v4 if ipaddress.ip_address(a).version == 4 else v6).append(a)
            except ValueError:
                continue
    return {"spf_name": cfg.get("spf_name", ""), "v4": v4, "v6": v6}


def check_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d{1,5})?", url):
        raise ClusterError("bad_url")
    return url


def leave_locally(db: Session) -> None:
    """Forget the cluster. The configuration stays; this server runs on its own."""
    db.execute(delete(ClusterNode))
    for key in (CONFIG_KEY, REVISION_KEY, PREVIOUS_SECRET_KEY, LAST_IMPORT_KEY):
        set_setting(db, key, None)
    set_secret(db, SECRET_KEY, None)
    set_secret(db, PREVIOUS_SECRET_KEY + ".value", None)


# ---- revisions -----------------------------------------------------------------
def revision(db: Session) -> dict | None:
    return get_setting(db, REVISION_KEY) or None


def rev_key(rev: dict | None) -> tuple[str, int, str]:
    """Revisions are ordered by time, then a counter, then node id (last change wins)."""
    if not rev:
        return ("", 0, "")
    return (str(rev.get("at", "")), int(rev.get("seq", 0)), str(rev.get("node", "")))


def bump_revision(db: Session) -> dict:
    cfg = config(db) or {}
    rev = {"at": now().isoformat(timespec="microseconds"), "node": cfg.get("node_id", "")}
    current = revision(db)
    if current and rev_key(current) >= rev_key(rev):  # clock went backwards: stay ahead anyway
        rev["at"] = current["at"]
        rev["seq"] = int(current.get("seq", 0)) + 1
    set_setting(db, REVISION_KEY, rev)
    return rev


def newer(a: dict | None, b: dict | None) -> bool:
    return rev_key(a) > rev_key(b)


# ---- join code -----------------------------------------------------------------
def join_code(db: Session) -> str:
    cfg = config(db)
    me = self_node(db)
    if not cfg or not me:
        raise ClusterError("not_in_cluster")
    raw = json.dumps({"u": me.url, "c": cfg["cluster_id"], "s": secret(db), "n": me.name}).encode()
    return JOIN_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_join_code(code: str) -> dict[str, str]:
    code = re.sub(r"\s+", "", code or "")
    if not code.startswith(JOIN_PREFIX):
        raise ClusterError("bad_join_code")
    body = code[len(JOIN_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        out = {"url": check_url(data["u"]), "cluster_id": str(data["c"]), "secret": str(data["s"]),
               "name": str(data.get("n", ""))}
    except (ValueError, KeyError, TypeError, ClusterError):
        raise ClusterError("bad_join_code") from None
    if len(out["secret"]) < 32:
        raise ClusterError("bad_join_code")
    return out


# ---- sealed messages -----------------------------------------------------------
def _fernet(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"mailfilter-cluster-v1:" + key.encode()).digest()))


def seal(key: str, purpose: str, data: dict[str, Any], *, nonce: str | None = None, reply_to: str | None = None) -> str:
    body: dict[str, Any] = {"p": purpose, "n": nonce or secrets.token_hex(16), "d": data}
    if reply_to:
        body["r"] = reply_to
    return _fernet(key).encrypt(json.dumps(body, separators=(",", ":"), default=str).encode()).decode()


def unseal(key: str, purpose: str, token: str | bytes, *, reply_to: str | None = None) -> dict[str, Any]:
    """Returns {"p", "n", "d", ...}. Raises ClusterError("auth_failed"|"clock"|"bad_message")."""
    f = _fernet(key)
    tok = token.encode() if isinstance(token, str) else token
    try:
        raw = f.decrypt(tok)
        ts = f.extract_timestamp(tok)
    except (InvalidToken, ValueError, TypeError):
        raise ClusterError("auth_failed") from None
    if abs(time.time() - ts) > MAX_AGE:
        raise ClusterError("clock", f"{int(time.time() - ts)}s")
    try:
        body = json.loads(raw)
    except ValueError:
        raise ClusterError("bad_message") from None
    if not isinstance(body, dict) or body.get("p") != purpose or not isinstance(body.get("d"), dict):
        raise ClusterError("bad_message")
    if reply_to is not None and body.get("r") != reply_to:
        raise ClusterError("bad_message", "answer does not belong to this request")
    return body


def remember_nonce(db: Session, nonce: str) -> None:
    """Each request is accepted once. Raises ClusterError("replay")."""
    if not re.fullmatch(r"[0-9a-f]{32}", nonce or ""):
        raise ClusterError("bad_message")
    db.execute(delete(ClusterNonce).where(ClusterNonce.seen_at < now() - timedelta(seconds=MAX_AGE * 2)))
    try:
        with db.begin_nested():
            db.add(ClusterNonce(nonce=nonce))
    except IntegrityError:
        raise ClusterError("replay") from None
