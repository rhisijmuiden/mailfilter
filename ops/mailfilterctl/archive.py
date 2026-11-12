"""Mail archive on disk: lock finished days, remove months whose retention has passed.

The web app (user mailfilter) writes archive files but cannot change them once a
day is over: `lock` sets the immutable flag (chattr +i) on every file and folder
of earlier days, so not even the mailfilter user can change or delete them.
Only root can take the flag off again, and this helper does that only for a
whole retention month that is over (`purge`), and around a restore.

Layout: <archive>/<domain>/<retain-until YYYY-MM>/<received YYYY-MM-DD>/<file>.eml.gz
"""
import datetime as dt
import os
import re
import shutil
from pathlib import Path

from . import paths
from .util import CtlError, have, run

DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
MONTH = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def root() -> Path:
    return Path(os.environ.get("MF_ARCHIVE_DIR") or (paths.root() / "var/lib/mailfilter-archive"))


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _chattr(flag: str, targets: list[Path], recursive: bool = False) -> None:
    if not targets or not have("chattr") or os.environ.get("MF_NO_CHATTR") == "1":
        return
    for i in range(0, len(targets), 500):
        run(["chattr", *(["-R"] if recursive else []), flag, *[str(t) for t in targets[i:i + 500]]], timeout=3600)


def _real_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink()


def lock(today: dt.date | None = None, force: bool = False) -> dict:
    """Make every day before today unchangeable (files first, then their folder).
    Days done before carry a .locked marker; force (after a restore) does them again."""
    today = today or _today()
    base = root()
    if not base.exists():
        return {"message": "No archive yet", "locked": 0}
    if not have("chattr") and os.environ.get("MF_NO_CHATTR") != "1":
        raise CtlError("chattr is not installed (package e2fsprogs)")
    locked = 0
    for dom in sorted(p for p in base.iterdir() if _real_dir(p) and DOMAIN.match(p.name)):
        for month in sorted(p for p in dom.iterdir() if _real_dir(p) and MONTH.match(p.name)):
            for day in sorted(p for p in month.iterdir() if _real_dir(p) and DAY.match(p.name)):
                if day.name >= today.isoformat() or ((day / ".locked").exists() and not force):
                    continue
                files = [f for f in day.iterdir() if f.is_file() and not f.is_symlink() and not f.name.startswith(".")]
                # Unfinished temporary files of a crashed delivery are not part of the archive.
                for f in day.iterdir():
                    if f.name.startswith(".") and f.name.endswith(".tmp"):
                        f.unlink()
                marker = day / ".locked"
                if not marker.exists():
                    marker.write_text(f"{dt.datetime.now(dt.timezone.utc).isoformat()}\n")
                try:
                    _chattr("+i", [*files, day / ".locked"])
                    _chattr("+i", [day])
                except CtlError as e:
                    _chattr("-i", [marker])
                    marker.unlink(missing_ok=True)
                    raise CtlError(f"Could not lock {day}: {e}. Does this file system support chattr +i?") from e
                locked += len(files)
    return {"message": f"Locked {locked} archived messages", "locked": locked}


def purge(domain: str, month: str, today: dt.date | None = None) -> dict:
    """Remove one domain's retention month after it is over. The web app decides
    which month (legal hold, chain); this only refuses anything still in retention."""
    today = today or _today()
    if not DOMAIN.match(domain or ""):
        raise CtlError(f"Not a valid domain: {domain}")
    if not MONTH.match(month or ""):
        raise CtlError(f"Not a valid month (YYYY-MM): {month}")
    if month >= f"{today:%Y-%m}":
        raise CtlError(f"The retention of {domain} {month} is not over yet")
    target = root() / domain / month
    if not target.exists():
        return {"message": f"Nothing to remove for {domain} {month}", "removed": 0}
    if not _real_dir(target) or target.resolve().parent.parent != root().resolve():
        raise CtlError(f"{target} is not an archive folder")
    count = sum(1 for f in target.rglob("*.eml.gz"))
    _chattr("-i", [target], recursive=True)
    shutil.rmtree(target)
    return {"message": f"Removed {count} archived messages of {domain} ({month})", "removed": count}


def unlock_all() -> None:
    """Before a restore: tar must be able to replace and remove archive files."""
    base = root()
    if base.exists():
        _chattr("-i", [base], recursive=True)
