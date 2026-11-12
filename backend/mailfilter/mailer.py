"""Mail the app sends itself: alerts, quarantine reports, password notices, backup reports.

Two ways to send, chosen in Settings > E-mail sending:

* ``local``: through this server's own Postfix (127.0.0.1:25), so the mail is
  DKIM-signed and filtered like everything else. This is the default.
* ``smtp``: through any SMTP server, with STARTTLS (587) or implicit TLS
  (465), TLS 1.2 or newer with certificate checks, and SMTP AUTH.

Every message gets Date, Message-ID and ``Auto-Submitted: auto-generated``
(RFC 3834) so autoresponders do not answer it, and both a plain text and an
optional HTML part. Non-ASCII addresses are sent with SMTPUTF8 when the server
supports it.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from dataclasses import asdict, dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Literal

from sqlalchemy.orm import Session

from .settings import get_settings
from .settings_store import get_secret, get_setting, set_secret, set_setting

SETTING_KEY = "smtp"
PASSWORD_KEY = "smtp.password"

Mode = Literal["local", "smtp"]
Security = Literal["starttls", "tls", "none"]


@dataclass
class SmtpConfig:
    mode: Mode = "local"
    host: str = ""
    port: int = 587
    security: Security = "starttls"
    username: str = ""
    verify_tls: bool = True
    from_address: str = ""
    from_name: str = "Mailfilter"
    reply_to: str = ""
    timeout: int = 20

    @property
    def sender(self) -> str:
        return self.from_address or f"mailfilter@{get_settings().hostname}"


class MailError(Exception):
    """Sending failed. ``code`` is a short reason the web interface translates."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def load_config(db: Session) -> SmtpConfig:
    data = get_setting(db, SETTING_KEY) or {}
    known = SmtpConfig.__dataclass_fields__
    return SmtpConfig(**{k: v for k, v in data.items() if k in known})


def has_password(db: Session) -> bool:
    return bool(get_setting(db, PASSWORD_KEY))


def save_config(db: Session, cfg: SmtpConfig, password: str | None = None, clear_password: bool = False) -> None:
    """``password=None`` keeps the stored password; ``clear_password`` removes it."""
    set_setting(db, SETTING_KEY, asdict(cfg))
    if clear_password:
        set_secret(db, PASSWORD_KEY, None)
    elif password:
        set_secret(db, PASSWORD_KEY, password)


def build_message(cfg: SmtpConfig, to: list[str], subject: str, text: str, html: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    local, _, domain = cfg.sender.rpartition("@")
    msg["From"] = Address(display_name=cfg.from_name or "", username=local, domain=domain)
    msg["To"] = ", ".join(to)
    if cfg.reply_to:
        msg["Reply-To"] = cfg.reply_to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain or get_settings().hostname)
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _tls_context(cfg: SmtpConfig) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if not cfg.verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _connect(cfg: SmtpConfig) -> smtplib.SMTP:
    if cfg.mode == "local":
        return smtplib.SMTP("127.0.0.1", 25, local_hostname=get_settings().hostname, timeout=cfg.timeout)
    if not cfg.host:
        raise MailError("smtp_no_host")
    if cfg.security == "tls":
        return smtplib.SMTP_SSL(cfg.host, cfg.port, local_hostname=get_settings().hostname,
                                timeout=cfg.timeout, context=_tls_context(cfg))
    s = smtplib.SMTP(cfg.host, cfg.port, local_hostname=get_settings().hostname, timeout=cfg.timeout)
    if cfg.security == "starttls":
        s.ehlo()
        if not s.has_extn("starttls"):
            s.close()
            raise MailError("smtp_no_starttls")
        s.starttls(context=_tls_context(cfg))
        s.ehlo()
    return s


def send(db: Session, to: list[str] | str, subject: str, text: str, html: str | None = None,
         cfg: SmtpConfig | None = None, password: str | None = None) -> str:
    """Send a message and return its Message-ID. Raises MailError with a translatable code."""
    cfg = cfg or load_config(db)
    to = [to] if isinstance(to, str) else list(to)
    if not to:
        raise MailError("smtp_no_recipient")
    msg = build_message(cfg, to, subject, text, html)
    try:
        with _connect(cfg) as s:
            if cfg.mode == "smtp" and cfg.username:
                secret = password if password is not None else get_secret(db, PASSWORD_KEY)
                s.login(cfg.username, secret or "")
            s.send_message(msg, from_addr=cfg.sender, to_addrs=to)
    except MailError:
        raise
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("smtp_auth_failed", _smtp_text(e)) from e
    except smtplib.SMTPNotSupportedError as e:
        raise MailError("smtp_not_supported", str(e)) from e
    except smtplib.SMTPRecipientsRefused as e:
        first = next(iter(e.recipients.values()), (0, b""))
        raise MailError("smtp_recipient_refused", f"{first[0]} {first[1].decode(errors='replace')}") from e
    except smtplib.SMTPSenderRefused as e:
        raise MailError("smtp_sender_refused", _smtp_text(e)) from e
    except smtplib.SMTPResponseException as e:
        raise MailError("smtp_rejected", _smtp_text(e)) from e
    except ssl.SSLCertVerificationError as e:
        raise MailError("smtp_tls_certificate", e.verify_message or str(e)) from e
    except ssl.SSLError as e:
        raise MailError("smtp_tls_failed", str(e)) from e
    except (socket.timeout, TimeoutError) as e:
        raise MailError("smtp_timeout") from e
    except (ConnectionRefusedError, socket.gaierror, OSError, smtplib.SMTPException) as e:
        raise MailError("smtp_connect_failed", str(e)) from e
    return msg["Message-ID"]


def _smtp_text(e: smtplib.SMTPResponseException) -> str:
    err = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
    return f"{e.smtp_code} {err}"
