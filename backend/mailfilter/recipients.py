"""Recipient check per transport domain: which addresses exist.

The mail server itself enforces it (mailfilter_config writes the Postfix
tables); this module turns database rows into mailfilter_config objects and
answers "does this address exist?" for the Test button, with the same method
Postfix uses:

* callout: a short SMTP conversation with the domain's mail server (EHLO,
  MAIL FROM, RCPT TO, never DATA), also with a made-up address to spot a
  server that accepts everything;
* ldap:    the Postfix LDAP client itself (`postmap -q`) with the same table file;
* list:    the saved list;
* off:     every address is accepted.
"""

from __future__ import annotations

import os
import secrets
import smtplib
import socket
import ssl
import subprocess
import tempfile
from dataclasses import asdict
from typing import Any, Callable

import mailfilter_config as mfc
from mailfilter_config.render import render_ldap_cf
from mailfilter_config.validate import ConfigError, check

from .settings import get_settings
from .settings_store import _fernet

LDAP_FIELDS = ("servers", "security", "verify_cert", "bind_dn", "base_dn", "preset", "query_filter", "result_attribute")
CACHE_KEY = "gateway.recipient_cache"


# ---- database <-> mailfilter_config ----------------------------------------------
def ldap_source(route) -> mfc.LdapSource | None:
    if not route.recipient_ldap:
        return None
    data = {k: v for k, v in route.recipient_ldap.items() if k in LDAP_FIELDS}
    return mfc.LdapSource(**data, bind_password=decrypt(route.recipient_ldap_password))


def domain_fields(route) -> dict[str, Any]:
    """Keyword arguments for mfc.Domain."""
    mode = route.recipient_check or ("callout" if route.verify_recipients else "off")
    return {
        "recipient_check": mode,
        "recipients": list(route.recipient_list or []) if mode == "list" else [],
        "ldap": ldap_source(route) if mode == "ldap" else None,
    }


def encrypt(value: str | None) -> str | None:
    return _fernet().encrypt(value.encode()).decode() if value else None


def decrypt(value: str | None) -> str:
    return _fernet().decrypt(value.encode()).decode() if value else ""


def cache(db) -> mfc.RecipientCache:
    from .settings_store import get_setting
    stored = get_setting(db, CACHE_KEY) or {}
    known = asdict(mfc.RecipientCache()).keys()
    return mfc.RecipientCache(**{k: v for k, v in stored.items() if k in known})


# ---- the Test button --------------------------------------------------------------
def test_address(domain: mfc.Domain, address: str, hostname: str | None = None,
                 smtp_factory: Callable[..., smtplib.SMTP] | None = None,
                 ldap_runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
                 mx_lookup: Callable[[str], list[str]] | None = None) -> dict[str, Any]:
    """{"method", "exists": True/False/None (None = could not tell), "detail", "accepts_all"}.

    `domain` must be validated (normalised address list and LDAP servers)."""
    mode = domain.recipient_check
    result: dict[str, Any] = {"method": mode, "address": address, "exists": None, "detail": "", "accepts_all": None}
    if mode == "off":
        result.update(exists=True, detail="no check: every address is accepted")
    elif mode == "list":
        result["exists"] = address in set(domain.recipients)
    elif mode == "ldap":
        result.update(ldap_lookup(domain, address, ldap_runner))
    else:
        result.update(callout(domain, address, hostname or get_settings().hostname, smtp_factory, mx_lookup))
    return result


def ldap_lookup(domain: mfc.Domain, address: str,
                runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None) -> dict[str, Any]:
    """Asks LDAP exactly like Postfix does: postmap -q with the same table file."""
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False))
    with tempfile.TemporaryDirectory(prefix="mf-ldap-") as tmp:
        path = os.path.join(tmp, "test.cf")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render_ldap_cf(domain.name, domain.ldap))
        try:
            res = run(["postmap", "-q", address, f"ldap:{path}"])
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"exists": None, "detail": str(e)}
    out = (res.stdout or "").strip()
    err = " ".join(ln.split(": ", 2)[-1] for ln in (res.stderr or "").splitlines() if ln.strip())
    err = err.replace(path, "ldap table")
    if res.returncode == 0 and out:
        return {"exists": True, "detail": out[:300]}
    if res.returncode == 1 and not err:
        return {"exists": False, "detail": ""}
    return {"exists": None, "detail": err[:500] or f"postmap exited with {res.returncode}"}


def _targets(domain: mfc.Domain, mx_lookup: Callable[[str], list[str]] | None) -> list[tuple[str, int, str]]:
    out = []
    for d in domain.destinations:
        if d.mx:
            hosts = (mx_lookup or _mx)(d.host) or [d.host]
            out += [(h, d.port, d.protocol) for h in hosts[:2]]
        else:
            out.append((d.host, d.port, d.protocol))
    return out


def _mx(name: str) -> list[str]:
    from mailfilter_dns.resolver import DnsError, DnsResolver
    try:
        return [m.host for m in sorted(DnsResolver().mx(name), key=lambda m: m.priority)]
    except DnsError:
        return []


def callout(domain: mfc.Domain, address: str, hostname: str,
            smtp_factory: Callable[..., smtplib.SMTP] | None = None,
            mx_lookup: Callable[[str], list[str]] | None = None) -> dict[str, Any]:
    """RCPT TO at the first mail server that answers, like Postfix's verify service."""
    errors = []
    for host, port, proto in _targets(domain, mx_lookup):
        try:
            factory = smtp_factory or (smtplib.LMTP if proto == "lmtp" else smtplib.SMTP)
            s = factory(host, port, local_hostname=hostname, timeout=15)
        except (OSError, smtplib.SMTPException) as e:
            errors.append(f"{host}:{port}: {e}")
            continue
        try:
            s.ehlo_or_helo_if_needed()
            if s.has_extn("starttls"):
                ctx = ssl.create_default_context()
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # Postfix uses opportunistic TLS too
                s.starttls(context=ctx)
                s.ehlo()
            code, msg = s.mail(f"double-bounce@{hostname}")
            if code >= 400:
                return {"exists": None, "server": f"{host}:{port}", "detail": f"MAIL FROM: {code} {_text(msg)}"}
            code, msg = s.rcpt(address)
            fake = f"mf-test-{secrets.token_hex(6)}@{domain.name}"
            fcode, _ = s.rcpt(fake)
            exists = True if code < 300 else False if code >= 500 else None
            return {"exists": exists, "server": f"{host}:{port}", "detail": f"{code} {_text(msg)}",
                    "accepts_all": fcode < 300, "fake_address": fake}
        except (OSError, smtplib.SMTPException, socket.timeout) as e:
            errors.append(f"{host}:{port}: {e}")
        finally:
            try:
                s.quit()
            except (OSError, smtplib.SMTPException):
                pass
    return {"exists": None, "detail": "; ".join(errors)[:500] or "no mail server to ask"}


def _text(msg: bytes | str) -> str:
    return (msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)).replace("\n", " ")[:300]


def validated(domain: mfc.Domain) -> mfc.Domain:
    """Validates one domain on its own (normalises it); raises ConfigError."""
    check(mfc.GatewayConfig(domains=[domain]))
    return domain


__all__ = ["ConfigError", "LDAP_FIELDS", "CACHE_KEY", "cache", "domain_fields", "test_address", "validated",
           "encrypt", "decrypt", "ldap_source"]
