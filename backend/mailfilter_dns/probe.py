"""Network probes: does a mail server answer on port 25, offer STARTTLS, and
does its certificate fit. Also the HTTPS certificate of Mailfilter itself."""

from __future__ import annotations

import datetime as dt
import smtplib
import socket
import ssl
from dataclasses import dataclass, field

from .resolver import norm


@dataclass
class CertInfo:
    names: list[str] = field(default_factory=list)
    not_after: str | None = None  # ISO date
    days_left: int | None = None
    matches: bool = False
    issuer: str = ""


@dataclass
class SmtpProbe:
    host: str
    ip: str
    port: int = 25
    reachable: bool = False
    banner: str = ""
    starttls: bool = False
    tls_ok: bool = False
    tls_version: str = ""
    cert: CertInfo | None = None
    error: str = ""


def cert_info(der: bytes | None, hostname: str) -> CertInfo | None:
    if not der:
        return None
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    c = x509.load_der_x509_certificate(der)
    names: list[str] = []
    try:
        san = c.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names = [norm(n) for n in san.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        names = [norm(a.value) for a in c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    end = c.not_valid_after_utc
    left = (end - dt.datetime.now(dt.timezone.utc)).days
    issuer = ", ".join(a.value for a in c.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME))
    return CertInfo(names, end.date().isoformat(), left, name_matches(hostname, names), issuer)


def name_matches(hostname: str, names: list[str]) -> bool:
    h = norm(hostname)
    for n in names:
        if n == h:
            return True
        if n.startswith("*.") and h.count(".") == n.count(".") and h.split(".", 1)[1] == n[2:]:
            return True
    return False


def _unverified_context() -> ssl.SSLContext:
    # We look at the certificate ourselves, so the handshake must not fail on it
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def smtp(host: str, ip: str | None = None, port: int = 25, timeout: float = 10.0, helo: str | None = None) -> SmtpProbe:
    """Connect to ``ip`` (or ``host``) and try STARTTLS. Never sends mail."""
    res = SmtpProbe(norm(host), ip or host, port)
    try:
        s = smtplib.SMTP(timeout=timeout, local_hostname=helo)
        code, msg = s.connect(ip or host, port)
    except (OSError, smtplib.SMTPException) as exc:
        res.error = _err(exc)
        return res
    res.reachable = code == 220
    res.banner = msg.decode("utf-8", "replace")[:200]
    try:
        s.ehlo()
        res.starttls = s.has_extn("starttls")
        if res.starttls:
            s.starttls(context=_unverified_context())
            res.tls_ok = True
            sock = s.sock
            if isinstance(sock, ssl.SSLSocket):
                res.tls_version = sock.version() or ""
                res.cert = cert_info(sock.getpeercert(binary_form=True), host)
        s.quit()
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        res.error = _err(exc)
        try:
            s.close()
        except Exception:
            pass
    return res


def https_cert(host: str, port: int = 443, timeout: float = 10.0) -> tuple[CertInfo | None, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with _unverified_context().wrap_socket(raw, server_hostname=host) as tls:
                return cert_info(tls.getpeercert(binary_form=True), host), ""
    except (OSError, ssl.SSLError) as exc:
        return None, _err(exc)


def _err(exc: Exception) -> str:
    if isinstance(exc, socket.timeout | TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    return f"{type(exc).__name__}: {exc}"[:200]
