"""Transport security for inbound mail: MTA-STS, TLS-RPT and DANE.

* MTA-STS (RFC 8461): a TXT record ``_mta-sts.<domain>`` with a policy id,
  and a policy file at ``https://mta-sts.<domain>/.well-known/mta-sts.txt``.
  Mailfilter hosts that file itself: ``mta-sts.<domain>`` is a CNAME to the
  server, Caddy gets a certificate for it on demand, and the web app answers
  with the policy stored here.
* TLS-RPT (RFC 8460): ``_smtp._tls.<domain>`` says where senders report TLS
  problems.
* DANE (RFC 7672): a TLSA record ``_25._tcp.<mx host>`` with the hash of the
  server's public key (3 1 1). It only counts when the zones are signed with
  DNSSEC, so that is checked as well. The certificate is renewed with the
  same key (certbot ``reuse_key``), so the record stays valid.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .config import STATE
from .resolver import DnsError, Resolver, norm

STS_FILE = STATE / "mta-sts.json"
CERT_DIR = Path("/etc/letsencrypt/live")
MODES = ("testing", "enforce", "none")
EMAIL_RE = re.compile(r"^[^@\s,;!]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$")


# --- MTA-STS policy --------------------------------------------------------------

@dataclass
class StsPolicy:
    domain: str
    mode: str = "testing"
    mx: list[str] = field(default_factory=list)
    max_age: int = 86400
    id: str = ""
    updated: str = ""

    def text(self) -> str:
        lines = ["version: STSv1", f"mode: {self.mode}", *(f"mx: {m}" for m in self.mx), f"max_age: {self.max_age}"]
        return "\r\n".join(lines) + "\r\n"

    @property
    def txt(self) -> str:
        return f"v=STSv1; id={self.id}"

    def to_dict(self) -> dict:
        return {**asdict(self), "txt": self.txt, "policy": self.text()}


def _load(path: Path | None = None) -> dict[str, dict]:
    path = path or STS_FILE
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def get_policy(domain: str, path: Path | None = None) -> StsPolicy | None:
    d = _load(path).get(norm(domain))
    return StsPolicy(**d) if d else None


def validate_mx(pattern: str) -> str:
    p = norm(pattern)
    body = p[2:] if p.startswith("*.") else p
    if not re.fullmatch(r"([a-z0-9_](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}", body or ""):
        raise ValueError(f"invalid_mx_pattern:{pattern[:100]}")
    return p


def save_policy(domain: str, mode: str, mx: list[str], max_age: int, path: Path | None = None) -> StsPolicy:
    """Store the policy. The id only changes when the policy itself changes,
    because senders re-fetch the file only when the id in DNS changes."""
    path = path or STS_FILE
    if mode not in MODES:
        raise ValueError("invalid_mode")
    pats = list(dict.fromkeys(validate_mx(m) for m in mx if m.strip()))
    if not pats and mode != "none":
        raise ValueError("no_mx")
    if not 86400 <= max_age <= 31557600:
        raise ValueError("invalid_max_age")
    domain = norm(domain)
    all_ = _load(path)
    old = StsPolicy(**all_[domain]) if domain in all_ else None
    new = StsPolicy(domain, mode, pats, max_age)
    if old and (old.mode, old.mx, old.max_age) == (new.mode, new.mx, new.max_age):
        return old
    now = dt.datetime.now(dt.timezone.utc)
    new.id = now.strftime("%Y%m%d%H%M%S")
    if old and new.id <= old.id:  # two changes in the same second
        new.id = str(int(old.id) + 1)
    new.updated = now.isoformat(timespec="seconds")
    all_[domain] = asdict(new)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(all_, indent=2))
    tmp.replace(path)
    return new


def policy_for_host(host: str, allowed: Callable[[str], bool], path: Path | None = None) -> StsPolicy | None:
    """The policy to serve for a request to ``mta-sts.<domain>``."""
    host = norm(host.split(":")[0])
    if not host.startswith("mta-sts."):
        return None
    domain = host[len("mta-sts."):]
    if not allowed(domain):
        return None
    return get_policy(domain, path)


def parse_policy(text: str) -> dict:
    out: dict = {"mx": []}
    for line in text.replace("\r\n", "\n").split("\n"):
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        if k == "mx":
            out["mx"].append(norm(v))
        elif k:
            out[k] = v
    return out


def mx_matches(host: str, patterns: list[str]) -> bool:
    host = norm(host)
    for p in patterns:
        if p == host or (p.startswith("*.") and host.endswith(p[1:]) and host.count(".") == p.count(".")):
            return True
    return False


def parse_sts_txt(v: str) -> dict[str, str]:
    tags = {}
    for part in v.split(";"):
        if "=" in part:
            k, val = part.split("=", 1)
            tags[k.strip().lower()] = val.strip()
    return tags


# --- TLS-RPT ---------------------------------------------------------------------

def tlsrpt_value(rua: list[str]) -> str:
    uris = []
    for a in rua:
        a = a.strip()
        if not a:
            continue
        if a.startswith("https://"):
            uris.append(a)
            continue
        a = a.removeprefix("mailto:")
        if not EMAIL_RE.match(a):
            raise ValueError(f"invalid_email:{a[:100]}")
        uris.append(f"mailto:{a}")
    if not uris:
        raise ValueError("no_rua")
    return "v=TLSRPTv1; rua=" + ",".join(uris)


def is_tlsrpt(v: str) -> bool:
    return v.replace(" ", "").lower().startswith("v=tlsrptv1")


# --- DANE ------------------------------------------------------------------------

def tlsa_name(host: str, port: int = 25) -> str:
    return f"_{port}._tcp.{norm(host)}"


def spki_sha256(pem: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    cert = x509.load_pem_x509_certificate(pem)
    spki = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def local_tlsa(host: str, cert_dir: Path | None = None) -> str | None:
    """``3 1 1 <hash>`` for the certificate Postfix uses, or None if there is none."""
    cert_dir = cert_dir or CERT_DIR
    for name in ("cert.pem", "fullchain.pem"):
        try:
            return f"3 1 1 {spki_sha256((cert_dir / norm(host) / name).read_bytes())}"
        except (FileNotFoundError, PermissionError, ValueError):
            continue
    return None


def norm_tlsa(v: str) -> str:
    parts = v.split()
    if len(parts) < 4:
        return v.strip().lower()
    return " ".join([*parts[:3], "".join(parts[3:]).lower()])


def signed(name: str, resolver: Resolver, rdtype: str = "A") -> bool | None:
    """True when DNS answers for ``name`` are DNSSEC-validated, None if unknown."""
    fn = getattr(resolver, "secure", None)
    if fn is None:
        return None
    try:
        return fn(name, rdtype)
    except DnsError:
        return None


@dataclass
class DaneStatus:
    host: str
    name: str
    expected: str | None  # from the local certificate
    published: list[str] = field(default_factory=list)  # as seen in DNS
    host_signed: bool | None = None
    domain_signed: bool | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.published:
            return "missing"
        if self.problems:
            return "bad" if any(p.startswith(("tlsa_mismatch", "dnssec_off")) for p in self.problems) else "weak"
        return "ok"

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status}


def dane_status(domain: str | None, host: str, resolver: Resolver, *, expected: str | None | bool = False) -> DaneStatus:
    host = norm(host)
    exp = local_tlsa(host) if expected is False else (expected or None)
    st = DaneStatus(host, tlsa_name(host), exp)
    try:
        st.published = [norm_tlsa(v) for v in resolver.tlsa(st.name)] if hasattr(resolver, "tlsa") else []
    except DnsError as exc:
        st.problems.append(f"dns_error:{exc}")
    st.host_signed = signed(host, resolver)
    if st.host_signed is False:
        st.problems.append(f"dnssec_off:{host}")
    if domain:
        st.domain_signed = signed(norm(domain), resolver, "MX")
        if st.domain_signed is False:
            st.problems.append(f"dnssec_off:{norm(domain)}")
    if st.published and exp and norm_tlsa(exp) not in st.published:
        st.problems.append("tlsa_mismatch")
    if not exp:
        st.problems.append("no_local_cert")
    return st
