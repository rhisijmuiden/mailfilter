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


@dataclass(frozen=True)
class Settings:
    hostname: str = "localhost"
    admin_email: str = ""
    public_v4: str = ""
    public_v6: str = ""
    source_dir: Path = Path("/opt/mailfilter")
    database_url: str = ""
    app_secret: str = ""
    rspamd_password: str = ""
    rspamd_controller: str = "http://127.0.0.1:11334"
    queue_helper: list[str] = field(default_factory=lambda: ["sudo", "-n", "/opt/mailfilter/backend/bin/mf-queuectl"])
    cookie_secure: bool = True
    session_hours: int = 12


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
        admin_email=env.get("MF_ADMIN_EMAIL", ""),
        public_v4=env.get("MF_PUBLIC_V4", ""),
        public_v6=env.get("MF_PUBLIC_V6", ""),
        source_dir=source,
        database_url=db_url,
        app_secret=env.get("APP_SECRET", ""),
        rspamd_password=env.get("RSPAMD_PASSWORD", ""),
        rspamd_controller=env.get("MF_RSPAMD_CONTROLLER", "http://127.0.0.1:11334"),
        queue_helper=helper.split() if helper else ["sudo", "-n", str(source / "backend/bin/mf-queuectl")],
        cookie_secure=env.get("MF_COOKIE_SECURE", "1") != "0",
        session_hours=int(env.get("MF_SESSION_HOURS", "12")),
    )
