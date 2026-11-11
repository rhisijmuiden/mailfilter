"""DKIM: the public key record Mailfilter signs with, and whether DNS matches it."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

from .resolver import DnsError, Resolver, norm

ETC = Path("/etc/mailfilter")
SELECTOR_MAP = ETC / "rspamd" / "dkim_selectors.map"
KEY_DIR = Path("/var/lib/rspamd/dkim")


@dataclass
class DkimKey:
    domain: str
    selector: str
    public_key: str  # base64 SubjectPublicKeyInfo, the p= value
    key_type: str = "rsa"

    @property
    def name(self) -> str:
        return f"{self.selector}._domainkey.{self.domain}"

    @property
    def record(self) -> str:
        return f"v=DKIM1; k={self.key_type}; p={self.public_key}"


def parse_record(txt: str) -> dict[str, str]:
    tags = {}
    for part in txt.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip().lower()] = re.sub(r"\s+", "", v)
    return tags


def public_key_from_private_pem(pem: bytes) -> tuple[str, str]:
    """Return (key type, base64 public key) for a PEM private key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

    key = serialization.load_pem_private_key(pem, password=None)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return "ed25519", base64.b64encode(raw).decode()
    if isinstance(key, rsa.RSAPrivateKey):
        der = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return "rsa", base64.b64encode(der).decode()
    raise ValueError("unsupported key type")


def parse_zone_snippet(text: str) -> str:
    """Join the quoted strings of an ``rspamadm dkim_keygen`` zone snippet."""
    parts = re.findall(r'"([^"]*)"', text)
    return "".join(parts) if parts else text.strip()


def selectors(map_file: Path = SELECTOR_MAP) -> dict[str, str]:
    """Domain -> selector from the Rspamd selector map the installer writes."""
    out: dict[str, str] = {}
    try:
        for line in map_file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                dom, _, sel = line.partition(" ")
                if sel.strip():
                    out[norm(dom)] = sel.strip()
    except FileNotFoundError:
        pass
    return out


def local_key(domain: str, selector: str | None = None, etc: Path = ETC, key_dir: Path = KEY_DIR) -> DkimKey | None:
    """Find the key Mailfilter signs ``domain`` with.

    Reads the private key when this process may (root, _rspamd), otherwise the
    zone snippet that the installer saved next to it in /etc/mailfilter.
    """
    domain = norm(domain)
    selector = selector or selectors(etc / "rspamd" / "dkim_selectors.map").get(domain)
    if not selector:
        return None
    key_file = key_dir / f"{domain}.{selector}.key"
    try:
        ktype, pub = public_key_from_private_pem(key_file.read_bytes())
        return DkimKey(domain, selector, pub, ktype)
    except (FileNotFoundError, PermissionError):
        pass
    snippet = etc / f"dkim-{domain}.{selector}.txt"
    try:
        tags = parse_record(parse_zone_snippet(snippet.read_text()))
    except (FileNotFoundError, PermissionError):
        return None
    if not tags.get("p"):
        return None
    return DkimKey(domain, selector, tags["p"], tags.get("k", "rsa"))


@dataclass
class DkimCheck:
    status: str  # ok, missing, mismatch, revoked, no_key, error
    published: str | None = None
    detail: str = ""


def verify(key: DkimKey | None, resolver: Resolver, domain: str | None = None) -> DkimCheck:
    if key is None:
        return DkimCheck("no_key")
    try:
        txts = resolver.txt(key.name)
    except DnsError as exc:
        return DkimCheck("error", detail=str(exc))
    recs = [t for t in txts if "p=" in t.replace(" ", "")]
    if not recs:
        return DkimCheck("missing")
    for t in recs:
        tags = parse_record(t)
        if tags.get("p") == key.public_key:
            return DkimCheck("ok", t)
    if any(parse_record(t).get("p") == "" for t in recs):
        return DkimCheck("revoked", recs[0])
    return DkimCheck("mismatch", recs[0])
