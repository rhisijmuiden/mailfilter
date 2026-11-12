"""Server facts the toolbox needs, read from what the installer writes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .resolver import norm

ETC = Path(os.environ.get("MF_ETC", "/etc/mailfilter"))
STATE = Path(os.environ.get("MF_STATE", "/var/lib/mailfilter"))


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except (FileNotFoundError, PermissionError):
        pass
    return out


@dataclass
class ServerConfig:
    hostname: str
    public_v4: list[str] = field(default_factory=list)
    public_v6: list[str] = field(default_factory=list)
    spf_name: str = ""  # TXT name that lists all Mailfilter addresses
    mx_priority: int = 10

    @property
    def spf_include(self) -> str:
        return f"include:{self.spf_name}"

    @property
    def addresses(self) -> list[str]:
        return [*self.public_v4, *self.public_v6]

    @classmethod
    def load(cls, etc: Path = ETC) -> "ServerConfig":
        env = {**read_env(etc / "mailfilter.env"), **{k: v for k, v in os.environ.items() if k.startswith("MF_")}}
        host = norm(env.get("MF_HOSTNAME", "") or os.uname().nodename)
        v4 = [a for a in env.get("MF_PUBLIC_V4", "").replace(",", " ").split() if a]
        v6 = [a for a in env.get("MF_PUBLIC_V6", "").replace(",", " ").split() if a]
        spf = norm(env.get("MF_SPF_NAME", "") or f"_spf.{host}")
        return cls(host, v4, v6, spf, int(env.get("MF_MX_PRIORITY", "10") or 10))


def relay_domains(etc: Path = ETC) -> dict[str, str | None]:
    """Domains Mailfilter accepts mail for, with their destination (or None)."""
    doms: dict[str, str | None] = {}
    for line in _lines(etc / "postfix" / "relay_domains"):
        doms[norm(line.split()[0])] = None
    for line in _lines(etc / "postfix" / "transport"):
        parts = line.split()
        if len(parts) >= 2 and norm(parts[0]) in doms:
            doms[norm(parts[0])] = parts[1]
    return doms


def destination_host(transport: str | None) -> tuple[str, int] | None:
    """``relay:[mail.example.nl]:25`` -> (``mail.example.nl``, 25)."""
    if not transport:
        return None
    nexthop = transport.split(":", 1)[1] if ":" in transport else transport
    port = 25
    if nexthop.startswith("["):
        host, _, rest = nexthop[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    else:
        host, _, p = nexthop.rpartition(":")
        if not host or not p.isdigit():
            host, p = nexthop, "25"
        port = int(p)
    return (host, port) if host else None


def _lines(path: Path) -> list[str]:
    try:
        return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    except (FileNotFoundError, PermissionError):
        return []
