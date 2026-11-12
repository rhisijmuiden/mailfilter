"""System events for alerts and integrations.

Every finished job and every remote copy emits an event. Events are
appended to /var/lib/mailfilterctl/events.jsonl (the newest 500 are kept)
and handed to each executable in /etc/mailfilter/hooks.d/, which is how
alerts (mail, Telegram, webhooks) plug in without changing this helper.

Hook contract
-------------
* A hook is an executable file in /etc/mailfilter/hooks.d/ owned by root and
  not writable by group or others (anything else is skipped). Files are run
  in name order; names starting with "." or ending in "~", ".dpkg-*" or
  ".disabled" are skipped.
* The event is passed as JSON on stdin and the event name in $MF_EVENT.
* Hooks run as root with a 60 second limit; their exit code and output only
  end up in the job log, they cannot make a job fail.

Event JSON: {"event": "<name>", "at": "<ISO time>", "host": "<fqdn>",
"ok": true|false, "message": "<English text>", ...extra fields}. Job events
add "job", "kind" and "params"; remote events after a backup add "backup".

Names:
  backup.succeeded / backup.failed       local backup (also the scheduled one)
  remote.succeeded / remote.failed       copy to the remote location (after a backup, or by hand)
  restore.succeeded / restore.failed     also a restore from the remote location (params.remote)
  update.succeeded / update.failed
"""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
from pathlib import Path

from . import paths
from .util import iso, now

KEEP = 500
SKIP_SUFFIXES = ("~", ".disabled", ".dpkg-old", ".dpkg-new", ".dpkg-dist", ".bak")


def hooks_dir() -> Path:
    return paths.etc() / "hooks.d"


def _log_path() -> Path:
    return paths.state() / "events.jsonl"


def _hooks() -> list[Path]:
    d = hooks_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.name.startswith(".") or p.name.endswith(SKIP_SUFFIXES):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or not os.access(p, os.X_OK):
            continue
        if os.geteuid() == 0 and (st.st_uid != 0 or st.st_mode & 0o022):
            continue  # only trust hooks that only root can change
        out.append(p)
    return out


def emit(name: str, ok: bool, message: str = "", log=None, **extra) -> dict:
    """Record an event and run the hooks. Never raises."""
    event = {"event": name, "at": iso(now()), "host": socket.getfqdn(), "ok": ok, "message": message, **extra}
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = p.read_text().splitlines()[-(KEEP - 1):] if p.exists() else []
        lines.append(json.dumps(event, sort_keys=True, default=str))
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.chmod(tmp, 0o640)
        os.replace(tmp, p)
    except OSError as e:
        if log:
            log(f"Could not record event {name}: {e}")
    data = json.dumps(event, default=str)
    for hook in _hooks():
        try:
            r = subprocess.run([str(hook)], input=data, capture_output=True, text=True, timeout=60,
                               env={**os.environ, "MF_EVENT": name})
            if log:
                log(f"Hook {hook.name} for {name}: exit {r.returncode}")
                for line in (r.stdout + r.stderr).strip().splitlines()[-10:]:
                    log(f"  {line}")
        except (OSError, subprocess.SubprocessError) as e:
            if log:
                log(f"Hook {hook.name} for {name} failed: {e}")
    return event


def recent(limit: int = 50) -> list[dict]:
    """Newest first."""
    p = _log_path()
    if not p.exists():
        return []
    out = []
    for line in reversed(p.read_text().splitlines()):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out
