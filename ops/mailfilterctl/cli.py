"""Command line interface. Add --json to any command for machine-readable output (used by the web app)."""
import argparse
import json
import os
import sys

from . import backup, jobs, system
from .util import CtlError, human_size


def _job_update(job: jobs.Job) -> dict:
    p = job.params
    return system.apply(p.get("branch"), backup=p.get("backup", True), log=job.log, step=job.step)


def _job_backup(job: jobs.Job) -> dict:
    p = job.params
    return backup.create(p.get("type", "auto"), label=p.get("label", ""), log=job.log, step=job.step)


def _job_restore(job: jobs.Job) -> dict:
    p = job.params
    return backup.restore(p["id"], components=p.get("components"), safety_backup=p.get("safety_backup", True),
                          log=job.log, step=job.step)


jobs.HANDLERS.update(update=_job_update, backup=_job_backup, restore=_job_restore)


def _launch(kind: str, params: dict, wait: bool) -> dict:
    if wait:
        os.environ["MF_JOB_ECHO"] = "1"
        job = jobs.create(kind, params)
        return jobs.run_job(job["id"])
    return jobs.start(kind, params)


def _print_human(data) -> None:
    if isinstance(data, dict) and "backups" in data and "config" in data:
        for b in data["backups"]:
            print(f"{b['id']}  {b['type']:<11}  {b['created']}  {b['size_human']:>9}  {b.get('label', '')}")
        print(f"\n{len(data['backups'])} backup(s), {human_size(data['total_size'])}, "
              f"{human_size(data['disk_free'])} free in {data['config']['location']}")
        return
    if isinstance(data, dict) and data.get("state") in ("queued", "running", "done", "failed"):
        print(f"Job {data['id']} ({data['kind']}): {data['state']}. {data.get('message', '')}".rstrip())
        return
    if isinstance(data, dict) and "message" in data and len(data) <= 3:
        print(data["message"])
        return
    print(json.dumps(data, indent=2))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mailfilterctl", description="RHIS Mailfilter system tasks")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="version and system information")
    sub.add_parser("version", help="print the version")

    up = sub.add_parser("update", help="check for and install updates").add_subparsers(dest="action", required=True)
    up.add_parser("check", help="look for a newer version on GitHub")
    ap_apply = up.add_parser("apply", help="install the newest version (makes a backup first)")
    ap_apply.add_argument("--branch", choices=sorted(system.BRANCHES), help="switch to this branch")
    ap_apply.add_argument("--no-backup", action="store_true")
    ap_apply.add_argument("--wait", action="store_true", help="run in the foreground")

    bk = sub.add_parser("backup", help="local backups").add_subparsers(dest="action", required=True)
    bk.add_parser("list")
    c = bk.add_parser("create")
    c.add_argument("--type", choices=["auto", "full", "incremental"], default="auto")
    c.add_argument("--label", default="")
    c.add_argument("--wait", action="store_true")
    v = bk.add_parser("verify")
    v.add_argument("id")
    d = bk.add_parser("delete", help="delete a full backup and its incrementals")
    d.add_argument("chain")
    cfg = bk.add_parser("config", help="show or change settings: backup config time=03:00 keep_full=6")
    cfg.add_argument("set", nargs="*", metavar="KEY=VALUE")
    bk.add_parser("schedule", help="(re)write the backup timer from the saved settings")

    rs = sub.add_parser("restore", help="restore a backup (makes a safety backup first)")
    rs.add_argument("id")
    rs.add_argument("--only", help="comma separated: files,database,redis")
    rs.add_argument("--no-safety-backup", action="store_true")
    rs.add_argument("--wait", action="store_true")

    jb = sub.add_parser("job", help="background tasks").add_subparsers(dest="action", required=True)
    jb.add_parser("list")
    js = jb.add_parser("status")
    js.add_argument("id")
    jl = jb.add_parser("log")
    jl.add_argument("id")
    jr = jb.add_parser("run", help=argparse.SUPPRESS)
    jr.add_argument("id")
    return ap


def dispatch(a) -> object:
    if a.cmd == "info":
        return system.info()
    if a.cmd == "version":
        return system.version_info()
    if a.cmd == "update":
        if a.action == "check":
            return system.check()
        return _launch("update", {"branch": a.branch, "backup": not a.no_backup}, a.wait)
    if a.cmd == "backup":
        if a.action == "list":
            return backup.summary()
        if a.action == "create":
            return _launch("backup", {"type": a.type, "label": a.label[:100]}, a.wait)
        if a.action == "verify":
            return backup.verify(a.id, log=lambda *_: None)
        if a.action == "delete":
            return backup.delete(a.chain)
        if a.action == "schedule":
            backup.set_config({})
            return {"message": "Backup schedule applied"}
        if a.action == "config":
            if not a.set:
                return backup.config()
            changes = {}
            for kv in a.set:
                if "=" not in kv:
                    raise CtlError(f"Use KEY=VALUE, not {kv}")
                k, v = kv.split("=", 1)
                changes[k] = v
            return backup.set_config(changes)
    if a.cmd == "restore":
        comps = [c.strip() for c in a.only.split(",")] if a.only else None
        backup._resolve(a.id)  # fail fast on a wrong id, before starting a job
        return _launch("restore", {"id": a.id, "components": comps, "safety_backup": not a.no_safety_backup},
                       a.wait)
    if a.cmd == "job":
        if a.action == "list":
            return {"jobs": jobs.list_jobs()}
        if a.action == "status":
            return jobs.get(a.id)
        if a.action == "log":
            return {"id": a.id, "log": jobs.log_text(a.id)}
        if a.action == "run":
            return jobs.run_job(a.id)
    raise CtlError("Unknown command")


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        data = dispatch(a)
    except CtlError as e:
        if a.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"mailfilterctl: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(data, indent=2, default=str))
    elif a.cmd == "job" and a.action == "log":
        print(data["log"])
    else:
        _print_human(data)
    if isinstance(data, dict) and data.get("state") == "failed":
        return 1
    return 0
