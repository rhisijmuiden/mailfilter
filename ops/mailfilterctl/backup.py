"""Local full and incremental backups, and restore.

Layout under the backup location (default /var/backups/mailfilter):

    <chain>/                       one chain starts with a full backup
        state.snar                 GNU tar snapshot used for the next incremental
        000/                       full backup
            manifest.json
            files.tar.zst          configuration (files.tar.gz when zstd is missing), DKIM keys, certificates, quarantine
            database.dump          PostgreSQL (pg_dump custom format, always complete)
            redis.rdb              Rspamd learning data (Bayes, fuzzy, greylisting)
        001/ 002/ ...              incremental backups: only files changed since the previous step

A backup id is ``<chain>.<step>``, for example ``20261111T023000Z.003``.
Restoring an incremental backup replays the full backup and every
incremental up to it, so the files end up exactly as they were at that moment.
The plain archives make it easy to copy them to FTP, FTPS or SCP later.
"""
import os
import re
import shutil
import socket
import tempfile
from pathlib import Path

from . import paths, system
from .util import CtlError, have, human_size, iso, now, parse_iso, read_json, run, sha256, write_json

# Paths relative to the file system root. /var/lib/mailfilter is left out on
# purpose: it holds the Python environment and job files, which the installer
# recreates, and an incremental restore would delete anything not in the backup.
FILE_SOURCES = [
    "etc/mailfilter",
    "var/lib/rspamd/dkim",
    "etc/letsencrypt",
    "var/spool/postfix/hold",
]
COMPONENTS = ("files", "database", "redis")
BACKUP_ID = re.compile(r"^(?P<chain>[0-9]{8}T[0-9]{6}Z)\.(?P<step>[0-9]{3})$")
CHAIN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
MAX_STEPS = 60  # start a new full backup after this many incrementals, whatever the age

DEFAULT_CONFIG = {
    "enabled": True,
    "location": "/var/backups/mailfilter",
    "time": "02:30",  # daily, server time
    "full_every_days": 7,
    "keep_full": 4,  # number of chains (full + its incrementals) to keep
}
CONFIG_TYPES = {"enabled": bool, "location": str, "time": str, "full_every_days": int, "keep_full": int}
TIMER_DROPIN = Path("/etc/systemd/system/mailfilter-backup.timer.d/schedule.conf")


# ---------------------------------------------------------------- settings

def config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(paths.etc() / "backup.json", {}) or {})
    return cfg


def set_config(changes: dict) -> dict:
    cfg = config()
    for key, value in changes.items():
        if key not in CONFIG_TYPES:
            raise CtlError(f"Unknown backup setting: {key}")
        typ = CONFIG_TYPES[key]
        if typ is bool and isinstance(value, str):
            value = value.lower() in ("1", "true", "yes", "on")
        try:
            value = typ(value)
        except (TypeError, ValueError) as e:
            raise CtlError(f"Invalid value for {key}: {value}") from e
        cfg[key] = value
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", cfg["time"]):
        raise CtlError("Backup time must look like 02:30")
    if not 1 <= cfg["full_every_days"] <= 90:
        raise CtlError("A full backup must be made every 1 to 90 days")
    if not 1 <= cfg["keep_full"] <= 100:
        raise CtlError("Keep between 1 and 100 full backups")
    loc = Path(cfg["location"])
    if not loc.is_absolute() or ".." in loc.parts or str(loc) in ("/", "/etc", "/var", "/usr", "/boot"):
        raise CtlError("Backup location must be a dedicated absolute folder, e.g. /var/backups/mailfilter")
    write_json(paths.etc() / "backup.json", cfg)
    _apply_schedule(cfg)
    return cfg


def _apply_schedule(cfg: dict) -> None:
    if paths.root() != Path("/") or not os.path.exists("/run/systemd/system") or os.geteuid() != 0:
        return
    TIMER_DROPIN.parent.mkdir(parents=True, exist_ok=True)
    TIMER_DROPIN.write_text(f"# Written by mailfilterctl from {paths.etc()}/backup.json\n"
                            f"[Timer]\nOnCalendar=\nOnCalendar=*-*-* {cfg['time']}:00\n")
    run(["systemctl", "daemon-reload"], check=False)
    run(["systemctl", "enable" if cfg["enabled"] else "disable", "--now", "mailfilter-backup.timer"], check=False)


def location() -> Path:
    return Path(os.environ.get("MF_BACKUP_DIR") or config()["location"])


def _skip() -> set[str]:
    return set(filter(None, os.environ.get("MF_BACKUP_SKIP", "").split(",")))


# ---------------------------------------------------------------- listing

def _chains() -> list[Path]:
    loc = location()
    if not loc.exists():
        return []
    return sorted(p for p in loc.iterdir() if p.is_dir() and CHAIN_ID.match(p.name))


def _steps(chain: Path) -> list[Path]:
    return sorted(p for p in chain.iterdir() if p.is_dir() and re.fullmatch(r"[0-9]{3}", p.name))


def list_backups() -> list[dict]:
    """All backups, newest first."""
    out = []
    for chain in _chains():
        for step in _steps(chain):
            m = read_json(step / "manifest.json")
            if m:
                m["size_human"] = human_size(m.get("size", 0))
                out.append(m)
    return sorted(out, key=lambda m: m["id"], reverse=True)


def _resolve(backup_id: str) -> tuple[Path, int]:
    m = BACKUP_ID.match(backup_id or "")
    if not m:
        raise CtlError(f"Not a valid backup id: {backup_id}")
    chain = location() / m["chain"]
    step = int(m["step"])
    if not (chain / f"{step:03d}" / "manifest.json").exists():
        raise CtlError(f"Backup not found: {backup_id}")
    return chain, step


def summary() -> dict:
    items = list_backups()
    loc = location()
    usage = shutil.disk_usage(loc if loc.exists() else loc.parent if loc.parent.exists() else "/")
    return {
        "config": config(),
        "backups": items,
        "total_size": sum(b.get("size", 0) for b in items),
        "disk_free": usage.free,
        "disk_total": usage.total,
        "last": items[0] if items else None,
    }


# ---------------------------------------------------------------- creating

def _archive(step_dir: Path) -> Path | None:
    for name in ("files.tar.zst", "files.tar.gz"):
        if (step_dir / name).exists():
            return step_dir / name
    return None


def _tar_sources() -> list[str]:
    override = os.environ.get("MF_BACKUP_SOURCES")
    sources = override.split(":") if override else FILE_SOURCES
    return [s for s in sources if (paths.root() / s).exists()]


def _dump_database(dest: Path, log) -> None:
    with open(dest, "wb") as f:
        os.chmod(dest, 0o600)
        run(["runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "mailfilter"],
            capture=False, stdout=f, timeout=3600, log=log)


def _dump_redis(dest: Path, log) -> None:
    run(["redis-cli", "--rdb", dest], timeout=600, log=log)
    os.chmod(dest, 0o600)


def choose_type(now_=None) -> str:
    """What the scheduled backup should be: full when needed, otherwise incremental."""
    chains = _chains()
    if not chains:
        return "full"
    last = chains[-1]
    full = read_json(last / "000" / "manifest.json")
    steps = _steps(last)
    if not full or not (last / "state.snar").exists() or len(steps) > MAX_STEPS:
        return "full"
    age = (now_ or now()) - parse_iso(full["created"])
    return "full" if age.days >= config()["full_every_days"] else "incremental"


def create(kind: str = "auto", label: str = "", log=print, step=None) -> dict:
    """Make a backup. kind is full, incremental or auto."""
    step = step or log
    if kind == "auto":
        kind = choose_type()
    if kind not in ("full", "incremental"):
        raise CtlError(f"Unknown backup type: {kind}")
    loc = location()
    loc.mkdir(parents=True, exist_ok=True)
    os.chmod(loc, 0o700)
    t = now()
    if kind == "incremental":
        chains = _chains()
        if not chains or not (chains[-1] / "state.snar").exists():
            log("No full backup to build on yet, making a full backup instead")
            kind = "full"
    if kind == "full":
        chain = loc / t.strftime("%Y%m%dT%H%M%SZ")
        if chain.exists():
            raise CtlError("A backup was started in this same second; try again")
        chain.mkdir()
        number = 0
    else:
        chain = _chains()[-1]
        number = len(_steps(chain))
    backup_id = f"{chain.name}.{number:03d}"
    step(f"{'Full' if kind == 'full' else 'Incremental'} backup {backup_id}")

    work = Path(tempfile.mkdtemp(prefix=f".tmp-{number:03d}-", dir=chain))
    try:
        files = {}
        skip = _skip()
        if "files" not in skip:
            sources = _tar_sources()
            step(f"Saving files: {', '.join('/' + s for s in sources)}")
            snar = work / "state.snar"
            if kind == "incremental":
                shutil.copy2(chain / "state.snar", snar)
            archive = "files.tar.zst" if have("zstd") else "files.tar.gz"
            run(["tar", "--create", "--zstd" if archive.endswith(".zst") else "--gzip", "--file", work / archive,
                 f"--listed-incremental={snar}", "--no-check-device", "--one-file-system",
                 "--warning=no-file-ignored", "--warning=no-file-changed",
                 "-C", paths.root(), *sources], timeout=7200, log=log)
            files[archive] = "files"
        if "database" not in skip:
            step("Saving the database")
            _dump_database(work / "database.dump", log)
            files["database.dump"] = "database"
        if "redis" not in skip:
            step("Saving spam filter learning data")
            _dump_redis(work / "redis.rdb", log)
            files["redis.rdb"] = "redis"

        info = system.version_info()
        manifest = {
            "id": backup_id,
            "chain": chain.name,
            "step": number,
            "type": kind,
            "label": label,
            "created": iso(t),
            "hostname": socket.getfqdn(),
            "version": info.get("version"),
            "branch": info.get("branch"),
            "commit": info.get("commit"),
            "components": sorted(files.values()),
            "files": {name: {"sha256": sha256(work / name), "size": (work / name).stat().st_size}
                      for name in files},
        }
        manifest["size"] = sum(f["size"] for f in manifest["files"].values())
        write_json(work / "manifest.json", manifest)
        snar_new = work / "state.snar"
        dest = chain / f"{number:03d}"
        if snar_new.exists():
            os.replace(snar_new, chain / "state.snar")
        os.rename(work, dest)
        os.chmod(dest, 0o700)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        if number == 0:
            shutil.rmtree(chain, ignore_errors=True)
        raise
    removed = prune(log)
    msg = f"Backup {backup_id} done ({human_size(manifest['size'])})"
    if removed:
        msg += f"; removed {len(removed)} old full backup(s)"
    log(msg)
    return {"message": msg, "backup": manifest, "removed": removed}


def prune(log=print) -> list[str]:
    """Remove the oldest chains beyond keep_full. The newest chain is never removed."""
    keep = max(1, config()["keep_full"])
    chains = _chains()
    removed = []
    for chain in chains[:-keep] if len(chains) > keep else []:
        shutil.rmtree(chain)
        removed.append(chain.name)
        log(f"Removed old backup chain {chain.name}")
    return removed


def delete(chain_id: str) -> dict:
    if not CHAIN_ID.match(chain_id or ""):
        raise CtlError("Give the id of a full backup (its chain), e.g. 20261111T023000Z")
    chain = location() / chain_id
    if not chain.exists():
        raise CtlError(f"Backup not found: {chain_id}")
    shutil.rmtree(chain)
    return {"message": f"Deleted backup {chain_id} and its incrementals"}


def verify(backup_id: str, log=print) -> dict:
    """Check checksums and that every archive up to this backup can be read."""
    chain, number = _resolve(backup_id)
    for i in range(number + 1):
        d = chain / f"{i:03d}"
        m = read_json(d / "manifest.json")
        if not m:
            raise CtlError(f"Backup {chain.name}.{i:03d} is missing; this backup cannot be restored")
        for name, meta in m["files"].items():
            if not (d / name).exists() or sha256(d / name) != meta["sha256"]:
                raise CtlError(f"{chain.name}.{i:03d}/{name} is damaged (checksum mismatch)")
        archive = _archive(d)
        if archive:
            # tar recognises the compression by itself when reading
            run(["tar", "--list", "--file", archive, "--listed-incremental=/dev/null"], timeout=3600)
    log(f"Backup {backup_id} is complete and readable")
    return {"message": f"Backup {backup_id} is complete and readable", "ok": True}


# ---------------------------------------------------------------- restoring

SERVICES_TO_STOP = ["mailfilter-web", "mailfilter-tracer", "postfix", "rspamd"]


def _systemctl(action: str, units: list[str], log) -> None:
    if paths.root() != Path("/") or not os.path.exists("/run/systemd/system"):
        return
    for u in units:
        run(["systemctl", action, u], check=False, log=log)


def restore(backup_id: str, components=None, safety_backup: bool = True, log=print, step=None) -> dict:
    step = step or log
    components = list(components or COMPONENTS)
    for c in components:
        if c not in COMPONENTS:
            raise CtlError(f"Unknown part to restore: {c}")
    chain, number = _resolve(backup_id)
    step("Checking the backup")
    verify(backup_id, log=log)
    target = read_json(chain / f"{number:03d}" / "manifest.json")

    if safety_backup:
        step("Making a safety backup of the current state first")
        create("full", label="before restore", log=log)

    step("Stopping mail services")
    _systemctl("stop", SERVICES_TO_STOP, log)
    try:
        if "files" in components:
            for i in range(number + 1):
                archive = _archive(chain / f"{i:03d}")
                if not archive:
                    continue
                step(f"Restoring files from {chain.name}.{i:03d}")
                # --listed-incremental=/dev/null replays deletions too, so the
                # folders end up exactly as they were at the time of the backup.
                run(["tar", "--extract", "--file", archive, "--listed-incremental=/dev/null",
                     "--same-owner", "--same-permissions", "-C", paths.root()], timeout=7200, log=log)
        if "database" in components and "database" in target["components"]:
            step("Restoring the database")
            _restore_database(chain / f"{number:03d}" / "database.dump", log)
        if "redis" in components and "redis" in target["components"]:
            step("Restoring spam filter learning data")
            _restore_redis(chain / f"{number:03d}" / "redis.rdb", log)
        if "files" in components and paths.root() == Path("/") and os.environ.get("MF_NO_RECONFIGURE") != "1":
            step("Applying the restored configuration")
            system.reconfigure(log)
    finally:
        step("Starting mail services")
        _systemctl("start", list(reversed(SERVICES_TO_STOP)), log)
    msg = f"Restored backup {backup_id} from {target['created']}"
    log(msg)
    return {"message": msg, "backup": target, "components": components}


def _restore_database(dump: Path, log) -> None:
    with open(dump, "rb") as f:
        run(["runuser", "-u", "postgres", "--", "pg_restore", "--clean", "--if-exists", "--no-owner",
             "--role=mailfilter", "--single-transaction", "-d", "mailfilter"],
            stdin=f, timeout=7200, log=log)


def _restore_redis(rdb: Path, log) -> None:
    run(["systemctl", "stop", "redis-server"], log=log)
    dest = Path("/var/lib/redis/dump.rdb")
    shutil.copy2(rdb, dest)
    shutil.chown(dest, "redis", "redis")
    os.chmod(dest, 0o660)
    run(["systemctl", "start", "redis-server"], log=log)
