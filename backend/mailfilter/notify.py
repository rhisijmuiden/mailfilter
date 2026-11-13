"""Alerts outside the web interface: e-mail, Telegram and a webhook.

A systemd timer (mailfilter-notify.timer -> ``mailfilter-admin notify-check``)
looks at the server every two minutes. Each problem it finds is a
*condition* with a stable key, for example ``services:postfix`` or
``blacklist:203.0.113.25:zen.spamhaus.org``. A condition is announced once
when it starts (after a short grace period for services, so a restart is not
an alert), again every ``repeat_hours`` while it lasts, and once more when it
is over ("resolved").

Some alerts are *events* instead of conditions: paused senders are announced
straight from the abuse scan (see abuse.py), there is nothing to resolve.

Which alert goes to which channel is a matrix in Settings > Notifications.
Secrets (Telegram bot token, webhook signing secret) are stored encrypted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .settings import get_settings
from .settings_store import get_secret, get_setting, set_secret, set_setting

log = logging.getLogger("mailfilter.notify")

SETTINGS_KEY = "notifications"
STATE_KEY = "notifications.state"
HISTORY_KEY = "notifications.history"
TELEGRAM_TOKEN = "notifications.telegram_token"
WEBHOOK_SECRET = "notifications.webhook_secret"
HISTORY_MAX = 100

Channel = Literal["email", "telegram", "webhook"]
CHANNELS: tuple[Channel, ...] = ("email", "telegram", "webhook")
Level = Literal["critical", "warning", "info"]

# Alert types, in the order the settings screen shows them.
TYPES = ("services", "queue", "blacklist", "certificate", "abuse", "backup", "link_click")

# Services the checker watches. Unlike the dashboard it also watches the web
# app itself: if that is down, nobody sees the dashboard.
SERVICES = ("postfix", "rspamd", "clamav-daemon", "redis-server", "postgresql", "unbound", "caddy",
            "mailfilter-tracer", "mailfilter-web", "crowdsec", "nftables")
SERVICE_GRACE = timedelta(minutes=3)


# ---- settings ---------------------------------------------------------------------------
@dataclass
class TypeSettings:
    channels: list[str] = field(default_factory=lambda: ["email"])
    threshold: int = 0  # meaning depends on the type, see DEFAULT_THRESHOLDS


DEFAULT_THRESHOLDS = {
    "queue": 200,        # deferred messages
    "certificate": 14,   # days before expiry
    "backup": 48,        # hours without a successful backup
}


@dataclass
class NotifySettings:
    language: Literal["en", "nl"] = "en"
    repeat_hours: int = 12
    send_resolved: bool = True
    email_enabled: bool = True
    email_to: list[str] = field(default_factory=list)  # empty = every active admin
    telegram_enabled: bool = False
    telegram_chat_id: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_format: Literal["json", "slack", "discord"] = "json"
    types: dict[str, TypeSettings] = field(default_factory=dict)

    def __post_init__(self):
        fixed: dict[str, TypeSettings] = {}
        for t in TYPES:
            v = self.types.get(t)
            if isinstance(v, dict):
                v = TypeSettings(**{k: x for k, x in v.items() if k in TypeSettings.__dataclass_fields__})
            if not isinstance(v, TypeSettings):
                v = TypeSettings(channels=list(CHANNELS))
            if not v.threshold and t in DEFAULT_THRESHOLDS:
                v.threshold = DEFAULT_THRESHOLDS[t]
            v.channels = [c for c in CHANNELS if c in v.channels]
            fixed[t] = v
        self.types = fixed

    def enabled(self, channel: str) -> bool:
        return bool(getattr(self, f"{channel}_enabled", False))


def load_settings(db: Session) -> NotifySettings:
    data = get_setting(db, SETTINGS_KEY) or {}
    known = NotifySettings.__dataclass_fields__
    return NotifySettings(**{k: v for k, v in data.items() if k in known})


def save_settings(db: Session, s: NotifySettings, telegram_token: str | None = None,
                  webhook_secret: str | None = None) -> None:
    """A secret of ``None`` keeps the stored one, ``""`` removes it."""
    set_setting(db, SETTINGS_KEY, asdict(s))
    if telegram_token is not None:
        set_secret(db, TELEGRAM_TOKEN, telegram_token or None)
    if webhook_secret is not None:
        set_secret(db, WEBHOOK_SECRET, webhook_secret or None)


# ---- messages ---------------------------------------------------------------------------
TEXTS: dict[str, dict[str, tuple[str, str]]] = {
    # code: {lang: (title, body)}
    "services": {
        "en": ("Service stopped: {service}", "The service {service} on {host} is not running ({state})."),
        "nl": ("Dienst gestopt: {service}", "De dienst {service} op {host} draait niet ({state})."),
    },
    "queue": {
        "en": ("Mail queue is filling up", "{deferred} messages are waiting to be delivered (alert from {threshold}). "
               "Active: {active}, on hold: {hold}."),
        "nl": ("Mailwachtrij loopt vol", "{deferred} berichten wachten op aflevering (melding vanaf {threshold}). "
               "Actief: {active}, vastgehouden: {hold}."),
    },
    "blacklist": {
        "en": ("Listed on {name}", "The address {ip} is on the blocklist {name} ({zone}). "
               "Other servers may refuse mail from it. Removal: {delist}"),
        "nl": ("Op blacklist {name}", "Het adres {ip} staat op de blacklist {name} ({zone}). "
               "Andere servers kunnen mail ervan weigeren. Verwijderen: {delist}"),
    },
    "certificate": {
        "en": ("Certificate expires in {days} days", "The certificate for {host} expires on {expires}. "
               "Renewal (certbot) should have happened by now; check 'certbot renew'."),
        "nl": ("Certificaat verloopt over {days} dagen", "Het certificaat voor {host} verloopt op {expires}. "
               "Het had al vernieuwd moeten zijn (certbot); controleer 'certbot renew'."),
    },
    "abuse": {
        "en": ("{count} sender(s) paused", "These senders suddenly sent spam or far more mail than usual. "
               "Their mail is kept on hold, nothing was lost or sent.\n{lines}"),
        "nl": ("{count} afzender(s) gepauzeerd", "Deze afzenders verstuurden ineens spam of veel meer mail dan normaal. "
               "Hun mail wordt vastgehouden, er is niets verloren of verstuurd.\n{lines}"),
    },
    "link_click": {
        "en": ("{what_en}: {host}", "{recipient} clicked a link in an e-mail to {url}.\n{what_en}. {reasons_en}"),
        "nl": ("{what_nl}: {host}", "{recipient} klikte in een e-mail op een link naar {url}.\n{what_nl}. {reasons_nl}"),
    },
    "backup_failed": {
        "en": ("Backup failed", "The backup of {created} failed: {message}"),
        "nl": ("Backup mislukt", "De backup van {created} is mislukt: {message}"),
    },
    "remote_failed": {
        "en": ("Remote backup copy failed", "The copy of the backup of {created} to the remote location failed: {message}"),
        "nl": ("Backup-kopie op afstand mislukt", "Het kopiëren van de backup van {created} naar de externe locatie is mislukt: {message}"),
    },
    "restore_failed": {
        "en": ("Restore failed", "The restore of {created} failed: {message}"),
        "nl": ("Herstel mislukt", "Het herstel van {created} is mislukt: {message}"),
    },
    "update_failed": {
        "en": ("Update failed", "The update of {created} failed and was rolled back: {message}"),
        "nl": ("Update mislukt", "De update van {created} is mislukt en teruggedraaid: {message}"),
    },
    "backup_overdue": {
        "en": ("No recent backup", "The last successful backup is from {last} (alert after {hours} hours)."),
        "nl": ("Geen recente backup", "De laatste geslaagde backup is van {last} (melding na {hours} uur)."),
    },
    "archive_integrity": {
        "en": ("Mail archive check found problems", "The integrity check of the mail archive found {count} problem(s): "
               "{summary}. Open Archive in the web interface for the details."),
        "nl": ("Controle mailarchief vond problemen", "De integriteitscontrole van het mailarchief vond {count} probleem/problemen: "
               "{summary}. Open Archief in de webinterface voor de details."),
    },
    "archive_failed": {
        "en": ("Mail archive maintenance failed", "The nightly archive work (seal, lock, clean-up) reported: {message}"),
        "nl": ("Onderhoud mailarchief mislukt", "Het nachtelijke archiefwerk (verzegelen, vergrendelen, opschonen) meldde: {message}"),
    },
    "test": {
        "en": ("Test notification", "This is a test from Mailfilter on {host}. This channel works."),
        "nl": ("Testmelding", "Dit is een test van Mailfilter op {host}. Dit kanaal werkt."),
    },
}
RESOLVED = {"en": "Resolved", "nl": "Opgelost"}
LINKS = {"link_click": "/links", "services": "/", "queue": "/trace?status=deferred", "blacklist": "/dns", "certificate": "/dns",
         "abuse": "/relay", "backup_failed": "/system/backups", "backup_overdue": "/system/backups",
         "remote_failed": "/system/backups", "restore_failed": "/system/backups", "update_failed": "/", "test": "/",
         "archive_integrity": "/archive", "archive_failed": "/archive"}
EMOJI = {"critical": "\U0001F534", "warning": "\U0001F7E0", "info": "\U0001F535", "resolved": "✅"}


class _Safe(dict):
    def __missing__(self, key):
        return "?"


@dataclass
class Alert:
    type: str      # settings type (services, queue, ...)
    code: str      # message code (backup_failed, ...)
    key: str       # stable identity of the condition
    level: Level
    params: dict[str, Any] = field(default_factory=dict)
    event: Literal["firing", "resolved", "test"] = "firing"

    def render(self, lang: str) -> tuple[str, str, str]:
        """(subject, body, link)"""
        host = get_settings().hostname
        title, body = TEXTS[self.code].get(lang, TEXTS[self.code]["en"])
        vals = _Safe({"host": host, **self.params})
        for k, v in self.params.items():  # language-specific values, e.g. lines_nl
            if k.endswith(f"_{lang}"):
                vals[k[: -len(lang) - 1]] = v
        title, body = title.format_map(vals), body.format_map(vals)
        if self.event == "resolved":
            title = f"{RESOLVED.get(lang, 'Resolved')}: {title}"
        return title, body, f"https://{host}{LINKS.get(self.code, '/')}"


# ---- channels ---------------------------------------------------------------------------
class ChannelError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _http() -> httpx.Client:
    return httpx.Client(timeout=15, follow_redirects=False)


def email_recipients(db: Session, s: NotifySettings, extra: list[str] | None = None) -> list[str]:
    from .models import User

    to = {a.strip().lower() for a in s.email_to if a.strip()}
    if not to:
        to = {e.lower() for e in db.execute(
            select(User.email).where(User.role == "admin", User.is_active.is_(True))).scalars()}
        if get_settings().admin_email:
            to.add(get_settings().admin_email.lower())
    to |= {a.strip().lower() for a in (extra or []) if a.strip()}
    return sorted(to)


def send_email(db: Session, s: NotifySettings, a: Alert, extra: list[str] | None = None) -> None:
    from .mailer import MailError, send

    to = email_recipients(db, s, extra)
    if not to:
        raise ChannelError("notify_no_recipients")
    subject, body, link = a.render(s.language)
    tag = {"critical": "[!]", "warning": "[!]"}.get(a.level, "") if a.event == "firing" else ""
    try:
        send(db, to, f"Mailfilter: {tag + ' ' if tag else ''}{subject}", f"{body}\n\n{link}\n")
    except MailError as e:
        raise ChannelError(e.code, e.detail) from e


def telegram_api(token: str, method: str, payload: dict | None = None) -> Any:
    if not token:
        raise ChannelError("telegram_no_token")
    try:
        with _http() as c:
            r = c.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {})
    except httpx.HTTPError as e:
        raise ChannelError("telegram_unreachable", type(e).__name__) from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 401 or r.status_code == 404:
        raise ChannelError("telegram_bad_token")
    if not data.get("ok"):
        raise ChannelError("telegram_error", str(data.get("description") or r.status_code))
    return data.get("result")


def send_telegram(db: Session, s: NotifySettings, a: Alert, token: str | None = None) -> None:
    token = token if token else get_secret(db, TELEGRAM_TOKEN)
    if not s.telegram_chat_id:
        raise ChannelError("telegram_no_chat")
    subject, body, link = a.render(s.language)
    mark = EMOJI["resolved"] if a.event == "resolved" else EMOJI.get(a.level, "")
    text = f"{mark} Mailfilter ({get_settings().hostname})\n{subject}\n\n{body}\n{link}"
    telegram_api(token or "", "sendMessage",
                 {"chat_id": s.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True})


def telegram_chats(token: str) -> list[dict]:
    """Chats that recently wrote to the bot, so the admin can pick one instead of looking up an id."""
    seen: dict[str, dict] = {}
    for u in telegram_api(token, "getUpdates", {"limit": 100}) or []:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                or chat.get("username") or str(chat["id"])
            seen[str(chat["id"])] = {"id": str(chat["id"]), "name": name, "type": chat.get("type", "")}
    return list(seen.values())


def webhook_payload(s: NotifySettings, a: Alert) -> dict:
    subject, body, link = a.render(s.language)
    text = f"{subject}\n{body}\n{link}"
    if s.webhook_format == "slack":
        return {"text": text}
    if s.webhook_format == "discord":
        return {"content": text[:1900]}
    return {
        "source": "mailfilter",
        "host": get_settings().hostname,
        "event": a.event,
        "type": a.type,
        "code": a.code,
        "key": a.key,
        "level": a.level,
        "title": subject,
        "message": body,
        "link": link,
        "params": a.params,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "text": text,
    }


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def send_webhook(db: Session, s: NotifySettings, a: Alert, secret: str | None = None) -> None:
    if not s.webhook_url.startswith(("https://", "http://")):
        raise ChannelError("webhook_no_url")
    secret = secret if secret is not None else get_secret(db, WEBHOOK_SECRET)
    body = json.dumps(webhook_payload(s, a), separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "Mailfilter"}
    if secret:
        ts = str(int(time.time()))
        headers["X-Mailfilter-Timestamp"] = ts
        headers["X-Mailfilter-Signature"] = sign(secret, ts, body)
    try:
        with _http() as c:
            r = c.post(s.webhook_url, content=body, headers=headers)
    except httpx.HTTPError as e:
        raise ChannelError("webhook_unreachable", type(e).__name__) from e
    if r.status_code >= 300:
        raise ChannelError("webhook_status", str(r.status_code))


SENDERS: dict[str, Callable[..., None]] = {"email": send_email, "telegram": send_telegram, "webhook": send_webhook}


def deliver(db: Session, s: NotifySettings, a: Alert, channels: list[str] | None = None,
            extra_email: list[str] | None = None) -> dict[str, str]:
    """Send one alert to its channels. Returns {channel: "ok" | error code}; never raises."""
    if channels is None:
        channels = [c for c in s.types[a.type].channels if s.enabled(c)] if a.type in s.types else []
    result: dict[str, str] = {}
    for ch in channels:
        try:
            if ch == "email":
                send_email(db, s, a, extra_email)
            else:
                SENDERS[ch](db, s, a)
            result[ch] = "ok"
        except ChannelError as e:
            result[ch] = f"{e.code}:{e.detail}" if e.detail else e.code
            log.error("notification %s via %s failed: %s", a.key, ch, e)
        except Exception as e:  # noqa: BLE001 - one broken channel must not stop the others
            result[ch] = f"error:{type(e).__name__}"
            log.exception("notification %s via %s failed", a.key, ch)
    if channels:
        _remember(db, a, result)
    return result


def _remember(db: Session, a: Alert, result: dict[str, str]) -> None:
    hist = list(get_setting(db, HISTORY_KEY) or [])
    hist.insert(0, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "type": a.type, "code": a.code,
                    "key": a.key, "level": a.level, "event": a.event, "params": a.params, "channels": result})
    set_setting(db, HISTORY_KEY, hist[:HISTORY_MAX])


def history(db: Session) -> list[dict]:
    return list(get_setting(db, HISTORY_KEY) or [])


def event(db: Session, a: Alert, extra_email: list[str] | None = None) -> dict[str, str]:
    """Announce a one-off event (no condition to resolve), e.g. paused senders."""
    s = load_settings(db)
    return deliver(db, s, a, extra_email=extra_email)


# ---- conditions -------------------------------------------------------------------------
def check_services() -> list[Alert]:
    import subprocess

    try:
        res = subprocess.run(["systemctl", "is-active", *SERVICES], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    states = res.stdout.split()
    if len(states) != len(SERVICES):
        return []
    return [Alert("services", "services", f"services:{svc}", "critical", {"service": svc, "state": st})
            for svc, st in zip(SERVICES, states) if st not in ("active", "activating", "reloading")]


def check_queue(threshold: int) -> list[Alert]:
    from .queuectl import QueueError, summary

    try:
        q = summary()
    except (QueueError, ValueError, OSError):
        return []
    deferred = q.get("deferred", 0)
    if deferred < threshold:
        return []
    level: Level = "critical" if deferred >= 5 * threshold else "warning"
    return [Alert("queue", "queue", "queue", level, {"deferred": deferred, "active": q.get("active", 0),
                                                     "hold": q.get("hold", 0), "threshold": threshold})]


def check_blacklist() -> list[Alert]:
    try:
        from mailfilter_dns import blacklist
        data = blacklist.load() or {}
    except Exception:  # noqa: BLE001 - no status file yet
        return []
    return [Alert("blacklist", "blacklist", f"blacklist:{x.get('ip')}:{x.get('zone')}", "critical",
                  {"ip": x.get("ip", ""), "zone": x.get("zone", ""), "name": x.get("name") or x.get("zone", ""),
                   "delist": x.get("delist", "")})
            for x in data.get("listed") or []]


def cert_file() -> Path:
    override = os.environ.get("MF_CERT_FILE")
    return Path(override) if override else Path(f"/etc/letsencrypt/live/{get_settings().hostname}/fullchain.pem")


def check_certificate(days: int, now: datetime) -> list[Alert]:
    from cryptography import x509

    try:
        cert = x509.load_pem_x509_certificate(cert_file().read_bytes())
    except (OSError, ValueError):
        return []
    expires = cert.not_valid_after_utc
    left = (expires - now).total_seconds() / 86400
    if left > days:
        return []
    level: Level = "critical" if left <= 3 else "warning"
    return [Alert("certificate", "certificate", "certificate", level,
                  {"days": max(0, int(left)), "expires": expires.strftime("%Y-%m-%d %H:%M UTC")})]


def _ctl_events() -> list[dict] | None:
    """Recent mailfilterctl events (newest first), or None on a helper without events."""
    from fastapi import HTTPException

    from . import ctl

    try:
        data = ctl.call("events", "--limit", "200")
    except HTTPException:
        return None
    return data.get("events") if isinstance(data, dict) and isinstance(data.get("events"), list) else None


def _when(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def check_backups(hours: int, now: datetime) -> list[Alert]:
    """Failed backup, remote copy, restore or update (from mailfilterctl events: the latest
    event of each kind decides), and no successful backup for `hours`."""
    from fastapi import HTTPException

    from . import ctl

    out: list[Alert] = []
    events = _ctl_events()
    if events is not None:
        for kind in ("backup", "remote", "restore", "update"):
            last = next((e for e in events if str(e.get("event", "")).startswith(kind + ".")), None)
            if last and not last.get("ok", True):
                when = _when(last.get("at"))
                out.append(Alert("backup", f"{kind}_failed", f"{kind}_failed", "critical",
                                 {"created": when.strftime("%Y-%m-%d %H:%M UTC") if when else "?",
                                  "message": last.get("message") or "?"}))
    try:
        if events is None:  # older helper: look at the job list instead
            jobs = [j for j in ctl.call("job", "list").get("jobs", []) if j.get("kind") == "backup"]
            finished = [j for j in jobs if j.get("state") in ("done", "failed")]
            if finished and finished[0].get("state") == "failed":
                j = finished[0]
                out.append(Alert("backup", "backup_failed", "backup_failed", "critical",
                                 {"created": (j.get("created") or "")[:16].replace("T", " "),
                                  "message": j.get("message") or "?"}))
        summary = ctl.call("backup", "list")
    except (HTTPException, AttributeError):
        return out
    cfg = summary.get("config") or {}
    when = _when((summary.get("last") or {}).get("created"))
    if cfg.get("enabled", True) and when and now - when > timedelta(hours=hours):
        out.append(Alert("backup", "backup_overdue", "backup_overdue", "warning",
                         {"last": when.strftime("%Y-%m-%d %H:%M UTC"), "hours": hours}))
    return out


def collect(s: NotifySettings, now: datetime) -> list[Alert]:
    """Every condition that is true right now, for the alert types that go somewhere."""
    def wanted(t: str) -> bool:
        return any(s.enabled(c) for c in s.types[t].channels)

    found: list[Alert] = []
    checks: list[tuple[str, Callable[[], list[Alert]]]] = [
        ("services", check_services),
        ("queue", lambda: check_queue(s.types["queue"].threshold)),
        ("blacklist", check_blacklist),
        ("certificate", lambda: check_certificate(s.types["certificate"].threshold, now)),
        ("backup", lambda: check_backups(s.types["backup"].threshold, now)),
    ]
    for t, fn in checks:
        if not wanted(t):
            continue
        try:
            found += fn()
        except Exception:  # noqa: BLE001 - one broken check must not stop the others
            log.exception("check %s failed", t)
    return found


def run_checks(db: Session, now: datetime | None = None,
               collector: Callable[[NotifySettings, datetime], list[Alert]] | None = None) -> list[Alert]:
    """One round of the timer: compare what is wrong now with the last round and send what changed.
    Returns the alerts that were sent."""
    now = now or datetime.now(timezone.utc)
    s = load_settings(db)
    current = {a.key: a for a in (collector or collect)(s, now)}
    state: dict[str, dict] = dict(get_setting(db, STATE_KEY) or {})
    sent: list[Alert] = []

    for key, a in current.items():
        st = state.get(key) or {"since": now.isoformat(), "sent": None, "type": a.type, "code": a.code}
        st.update(level=a.level, params=a.params)
        since = datetime.fromisoformat(st["since"])
        grace = SERVICE_GRACE if a.type == "services" else timedelta(0)
        last = datetime.fromisoformat(st["sent"]) if st.get("sent") else None
        due = last is None and now - since >= grace
        if last and s.repeat_hours and now - last >= timedelta(hours=s.repeat_hours):
            due = True
        if due:
            deliver(db, s, a)
            st["sent"] = now.isoformat()
            sent.append(a)
        state[key] = st

    for key in [k for k in state if k not in current]:
        st = state.pop(key)
        if st.get("sent") and s.send_resolved and st.get("code") in TEXTS:
            a = Alert(st["type"], st["code"], key, "info", st.get("params") or {}, event="resolved")
            deliver(db, s, a)
            sent.append(a)

    set_setting(db, STATE_KEY, state)
    return sent


def active(db: Session) -> list[dict]:
    """Conditions the checker currently knows about (for the settings screen)."""
    state = get_setting(db, STATE_KEY) or {}
    return [{"key": k, **v} for k, v in sorted(state.items(), key=lambda kv: kv[1].get("since", ""))]
