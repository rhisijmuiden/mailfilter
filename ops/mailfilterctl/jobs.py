"""Background jobs: updates, backups and restores.

A job is a JSON status file in /var/lib/mailfilterctl/jobs/<id>.json plus a log
file next to it. ``start`` launches ``mailfilterctl job run <id>`` as a
transient systemd unit, so the job keeps running when the web app that asked
for it is restarted (which an update does). Only one job runs at a time.
"""
import fcntl
import os
import re
import secrets
import subprocess
import sys
import traceback
from pathlib import Path

from . import paths
from .util import CtlError, iso, now, read_json, write_json

JOB_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9]{6}$")
KINDS = {"update", "backup", "restore"}
KEEP_JOBS = 100

# Filled by cli.py to avoid an import cycle: kind -> function(job) -> message
HANDLERS: dict = {}


def _status_path(job_id: str) -> Path:
    if not JOB_ID.match(job_id):
        raise CtlError(f"Not a valid job id: {job_id}")
    return paths.jobs() / f"{job_id}.json"


def get(job_id: str) -> dict:
    data = read_json(_status_path(job_id))
    if data is None:
        raise CtlError(f"Job not found: {job_id}")
    return data


def log_text(job_id: str, tail: int = 500) -> str:
    p = _status_path(job_id).with_suffix(".log")
    if not p.exists():
        return ""
    return "\n".join(p.read_text(errors="replace").splitlines()[-tail:])


def list_jobs(limit: int = 20) -> list[dict]:
    d = paths.jobs()
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), reverse=True)[:limit]
    return [j for j in (read_json(f) for f in files) if j]


def running() -> dict | None:
    for j in list_jobs(10):
        if j["state"] in ("queued", "running"):
            return j
    return None


def _prune() -> None:
    files = sorted(paths.jobs().glob("*.json"), reverse=True)
    for f in files[KEEP_JOBS:]:
        f.unlink(missing_ok=True)
        f.with_suffix(".log").unlink(missing_ok=True)


def create(kind: str, params: dict) -> dict:
    if kind not in KINDS:
        raise CtlError(f"Unknown job kind: {kind}")
    busy = running()
    if busy:
        raise CtlError(f"Another task is still running ({busy['kind']}, started {busy['created']}). Try again when it is done.")
    paths.jobs().mkdir(parents=True, exist_ok=True)
    t = now()
    job = {
        "id": f"{t.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}",
        "kind": kind,
        "params": params,
        "state": "queued",
        "created": iso(t),
        "started": None,
        "finished": None,
        "message": "",
        "steps": [],
        "result": None,
    }
    write_json(_status_path(job["id"]), job)
    _prune()
    return job


def start(kind: str, params: dict) -> dict:
    """Create a job and run it in the background."""
    job = create(kind, params)
    cmd = paths.ctl_command() + ["job", "run", job["id"]]
    if os.environ.get("MF_JOBS_INLINE") == "1":
        run_job(job["id"])
    elif os.path.exists("/run/systemd/system") and os.geteuid() == 0:
        subprocess.run(
            ["systemd-run", f"--unit=mailfilter-job-{job['id']}", "--collect", "--quiet",
             f"--description=Mailfilter {kind} job", *cmd],
            check=True, capture_output=True, text=True,
        )
    else:
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return get(job["id"])


class Job:
    """Handle given to job handlers for reporting progress."""

    def __init__(self, data: dict):
        self.data = data
        self.id = data["id"]
        self.params = data["params"]
        self._log = _status_path(self.id).with_suffix(".log").open("a")

    def save(self) -> None:
        write_json(_status_path(self.id), self.data)

    def log(self, line: str) -> None:
        self._log.write(f"{iso(now())} {line}\n")
        self._log.flush()
        if os.environ.get("MF_JOB_ECHO") == "1":
            print(line, file=sys.stderr)

    def step(self, text: str) -> None:
        """A user-visible step, shown as a progress line in the web interface."""
        self.data["steps"].append({"at": iso(now()), "text": text})
        self.log(f"== {text}")
        self.save()


def run_job(job_id: str) -> dict:
    data = get(job_id)
    if data["state"] != "queued":
        raise CtlError(f"Job {job_id} already {data['state']}")
    paths.jobs().mkdir(parents=True, exist_ok=True)
    lock = open(paths.jobs() / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        data.update(state="failed", finished=iso(now()), message="Another task is still running.")
        write_json(_status_path(job_id), data)
        return data
    job = Job(data)
    data.update(state="running", started=iso(now()))
    job.save()
    try:
        result = HANDLERS[data["kind"]](job)
        data.update(state="done", message=(result or {}).get("message", "Done"), result=result)
    except CtlError as e:
        job.log(f"FAILED: {e}")
        data.update(state="failed", message=str(e))
    except Exception as e:  # noqa: BLE001 - report anything to the UI instead of dying silently
        job.log(traceback.format_exc())
        data.update(state="failed", message=f"Unexpected error: {e}")
    data["finished"] = iso(now())
    job.save()
    lock.close()
    return data
