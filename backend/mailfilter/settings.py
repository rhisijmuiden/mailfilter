"""Runtime settings.

The installer writes /etc/mailfilter/mailfilter.env (plain settings) and
/etc/mailfilter/secrets.env (passwords and keys). Environment variables win,
so tests and development can override anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ETC = Path(os.environ.get("MF_ETC", "/etc/mailfilter"))


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text()
    except (FileNotFoundError, PermissionError):
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


CONFIG_HELPER_PREFIX = ["sudo", "-n", "/usr/bin/systemd-run", "--quiet", "--pipe", "--wait", "--collect",
                        "--service-type=exec"]


@dataclass(frozen=True)
class Settings:
    hostname: str = "localhost"
    # Address users open the web interface at (install/lib/web.sh): https://<host>, a reverse proxy's, or http://.
    web_url: str = ""
    admin_email: str = ""
    public_v4: str = ""
    public_v6: str = ""
    source_dir: Path = Path("/opt/mailfilter")
    database_url: str = ""
    app_secret: str = ""
    rspamd_password: str = ""
    rspamd_controller: str = "http://127.0.0.1:11334"
    queue_helper: list[str] = field(default_factory=lambda: ["sudo", "-n", "/opt/mailfilter/backend/bin/mf-queuectl"])
    # Applies domains, relay access and throttling as root. The web app runs with
    # ProtectSystem=full (read-only /etc), so the helper is started by systemd-run
    # outside that sandbox; sudoers allows exactly this command plus "apply --stdin --json".
    config_helper: list[str] = field(default_factory=list)
    cookie_secure: bool = True
    session_hours: int = 12
    # Used to bucket charts per hour/day in local time.
    timezone: str = "Europe/Amsterdam"
    # Allow/block list maps the web app writes and Rspamd reads (multimap.conf).
    lists_dir: Path = Path("/var/lib/mailfilter/lists")
    # Mailtrap mailboxes (Maildir per mailbox login), written by the mftrap pipe.
    mailtrap_dir: Path = Path("/var/lib/mailfilter/mailtrap")
    # Signs the click links of link protection (shared with Rspamd).
    link_key_file: Path = ETC / "link-protection.key"
    # Mail archive (gzip files per domain), written by the mfarchive pipe. Outside
    # /var/lib/mailfilter on purpose: it is part of every backup.
    archive_dir: Path = Path("/var/lib/mailfilter-archive")


@lru_cache
def get_settings() -> Settings:
    env = {**_read_env_file(ETC / "mailfilter.env"), **_read_env_file(ETC / "secrets.env"), **os.environ}
    db_url = env.get("MAILFILTER_DB_URL") or (
        f"postgresql+psycopg://mailfilter:{env.get('DB_PASSWORD', '')}@127.0.0.1/mailfilter"
    )
    source = Path(env.get("MF_SOURCE", "/opt/mailfilter"))
    helper = env.get("MF_QUEUE_HELPER")
    return Settings(
        hostname=env.get("MF_HOSTNAME", "localhost"),
        web_url=(env.get("MF_WEB_URL") or f"https://{env.get('MF_HOSTNAME', 'localhost')}").rstrip("/"),
        admin_email=env.get("MF_ADMIN_EMAIL", ""),
        public_v4=env.get("MF_PUBLIC_V4", ""),
        public_v6=env.get("MF_PUBLIC_V6", ""),
        source_dir=source,
        database_url=db_url,
        app_secret=env.get("APP_SECRET", ""),
        rspamd_password=env.get("RSPAMD_PASSWORD", ""),
        rspamd_controller=env.get("MF_RSPAMD_CONTROLLER", "http://127.0.0.1:11334"),
        queue_helper=helper.split() if helper else ["sudo", "-n", str(source / "backend/bin/mf-queuectl")],
        config_helper=env["MF_CONFIG_HELPER"].split() if env.get("MF_CONFIG_HELPER") else
        [*CONFIG_HELPER_PREFIX, str((source / "backend/bin/mailfilter-config").resolve())],
        cookie_secure=env.get("MF_COOKIE_SECURE", "1") != "0",
        session_hours=int(env.get("MF_SESSION_HOURS", "12")),
        timezone=env.get("MF_TIMEZONE", "Europe/Amsterdam"),
        lists_dir=Path(env.get("MF_LISTS_DIR", "/var/lib/mailfilter/lists")),
        mailtrap_dir=Path(env.get("MF_MAILTRAP_DIR", "/var/lib/mailfilter/mailtrap")),
        link_key_file=Path(env.get("MF_LINK_KEY_FILE", str(ETC / "link-protection.key"))),
        archive_dir=Path(env.get("MF_ARCHIVE_DIR", "/var/lib/mailfilter-archive")),
    )
