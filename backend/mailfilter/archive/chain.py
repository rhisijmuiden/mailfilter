"""Hash chain and daily seals: how the archive proves that nothing changed.

Every archived message gets an entry hash over its fingerprint (SHA-256 of the
message) and its key facts, plus the entry hash of the message before it in the
same domain. Changing, removing or inserting one message breaks every hash after it.

Once a day the chain head of each domain is sealed: the seal holds the first and
last sequence number of that day, the chain head, a digest of the envelope
recipients (filled in from the mail log during the day) and the previous seal,
and is signed with this server's Ed25519 archive key. The public key is shown in
the web interface and included in every export, so anyone can check a seal.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy.orm import Session

from ..settings_store import get_secret, get_setting, set_secret, set_setting

ZERO = "0" * 64
KEY_SECRET = "archive.seal_key"  # private key (encrypted with APP_SECRET)
KEY_PUBLIC = "archive.seal_public"  # {"key": hex, "id": first 16 hex of its SHA-256}


def retain_until(received: date, years: int, floor: date | None = None) -> date:
    """Kept at least `years` years: until the end of the month, `years` later.
    Never earlier than `floor` (the latest date given out so far for this domain),
    so clean-ups always remove the oldest part of the chain."""
    y, m = received.year + years, received.month
    end = date(y, m, calendar.monthrange(y, m)[1])
    return max(end, floor) if floor else end


def entry_hash(*, domain: str, seq: int, received_at: datetime, direction: str, queue_id: str, sender: str,
               sha256: str, size: int, retain_until: date, prev_hash: str) -> str:
    text = "\n".join([
        "mfarchive-v1", domain, str(seq), received_at.isoformat(timespec="microseconds"), direction, queue_id,
        sender, sha256, str(size), retain_until.isoformat(), prev_hash,
    ])
    return hashlib.sha256(text.encode()).hexdigest()


def entry_hash_of(m) -> str:
    return entry_hash(domain=m.domain, seq=m.seq, received_at=m.received_at, direction=m.direction,
                      queue_id=m.queue_id, sender=m.sender, sha256=m.sha256, size=m.size,
                      retain_until=m.retain_until, prev_hash=m.prev_hash)


def envelope_digest(rows: list[tuple[int, list[str]]]) -> str:
    h = hashlib.sha256()
    for seq, rcpts in sorted(rows):
        h.update(f"{seq}:{','.join(sorted(rcpts))}\n".encode())
    return h.hexdigest()


def seal_payload(*, domain: str, day: date, first_seq: int, last_seq: int, count: int, bytes_: int,
                 chain_head: str, envelope_digest: str, prev_seal: str) -> str:
    return json.dumps({"v": 1, "domain": domain, "day": day.isoformat(), "first_seq": first_seq,
                       "last_seq": last_seq, "count": count, "bytes": bytes_, "chain_head": chain_head,
                       "envelope_digest": envelope_digest, "prev_seal": prev_seal},
                      sort_keys=True, separators=(",", ":"))


def seal_hash_of(s) -> str:
    return hashlib.sha256(seal_payload(
        domain=s.domain, day=s.day, first_seq=s.first_seq, last_seq=s.last_seq, count=s.count, bytes_=s.bytes,
        chain_head=s.chain_head, envelope_digest=s.envelope_digest, prev_seal=s.prev_seal).encode()).hexdigest()


# ---- the archive key ----------------------------------------------------------------
def _key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def signing_key(db: Session) -> tuple[Ed25519PrivateKey, str]:
    """This server's archive key; made on first use."""
    pem = get_secret(db, KEY_SECRET)
    if pem:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        assert isinstance(key, Ed25519PrivateKey)
    else:
        key = Ed25519PrivateKey.generate()
        set_secret(db, KEY_SECRET, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()).decode())
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub = {"key": raw.hex(), "id": _key_id(raw)}
    keys = get_setting(db, KEY_PUBLIC) or {}
    if keys.get("key") != pub["key"]:
        # Keep earlier public keys (after a restore on a new server) so old seals still verify.
        old = [k for k in keys.get("previous", []) if k.get("key") != pub["key"]]
        if keys.get("key"):
            old.append({"key": keys["key"], "id": keys.get("id", "")})
        set_setting(db, KEY_PUBLIC, {**pub, "previous": old})
    return key, pub["id"]


def public_keys(db: Session) -> dict[str, str]:
    """{key id: public key hex} for this key and earlier ones."""
    keys = get_setting(db, KEY_PUBLIC) or {}
    out = {k["id"]: k["key"] for k in keys.get("previous", []) if k.get("id")}
    if keys.get("key"):
        out[keys["id"]] = keys["key"]
    return out


def sign(db: Session, seal_hash: str) -> tuple[str, str]:
    key, kid = signing_key(db)
    return key.sign(bytes.fromhex(seal_hash)).hex(), kid


def check_signature(public_hex: str, seal_hash: str, signature_hex: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex)).verify(bytes.fromhex(signature_hex),
                                                                             bytes.fromhex(seal_hash))
        return True
    except Exception:
        return False
