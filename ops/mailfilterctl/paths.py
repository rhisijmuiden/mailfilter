"""File locations. Every path can be overridden through the environment, which the tests use."""
import os
from pathlib import Path


def _env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def source() -> Path:
    """Git checkout of Mailfilter."""
    if "MF_SOURCE" in os.environ:
        return Path(os.environ["MF_SOURCE"])
    return Path(__file__).resolve().parents[2]


def etc() -> Path:
    return _env("MF_ETC", "/etc/mailfilter")


def state() -> Path:
    return _env("MF_STATE", "/var/lib/mailfilterctl")


def jobs() -> Path:
    return state() / "jobs"


def root() -> Path:
    """File system root that backups are taken from and restored to."""
    return _env("MF_ROOT", "/")


def ctl_command() -> list[str]:
    """How to start mailfilterctl again, for background jobs."""
    installed = Path("/usr/local/sbin/mailfilterctl")
    if os.environ.get("MF_CTL"):
        return [os.environ["MF_CTL"]]
    if installed.exists():
        return [str(installed)]
    return [os.environ.get("MF_PYTHON", "python3"), str(source() / "ops" / "bin" / "mailfilterctl")]
