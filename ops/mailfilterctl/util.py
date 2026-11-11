import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class CtlError(Exception):
    """An error with a message that is safe and useful to show in the web interface."""


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def run(cmd, *, check=True, capture=True, timeout=None, input=None, log=None, **kw) -> subprocess.CompletedProcess:
    """Run a command. On failure raise CtlError with the last lines of its output."""
    if log:
        log(f"$ {' '.join(map(str, cmd))}")
    try:
        p = subprocess.run(
            [str(c) for c in cmd], capture_output=capture, text=True, timeout=timeout, input=input, **kw
        )
    except FileNotFoundError as e:
        raise CtlError(f"Program not found: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise CtlError(f"Timed out after {timeout}s: {' '.join(map(str, cmd))}") from e
    if log and capture:
        for line in (p.stdout + p.stderr).splitlines()[-200:]:
            log(f"  {line}")
    if check and p.returncode != 0:
        tail = "\n".join(((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-5:]) if capture else ""
        raise CtlError(f"Command failed ({p.returncode}): {' '.join(map(str, cmd))}\n{tail}".strip())
    return p


def have(program: str) -> bool:
    return shutil.which(program) is not None


def write_json(path: Path, data) -> None:
    """Write JSON atomically so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
