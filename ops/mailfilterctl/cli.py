"""Command line interface. Add --json to any command for machine-readable output (used by the web app)."""
import argparse
import json
import os
import sys

from . import backup, events, jobs, remote, system
from .util import CtlError, human_size


def _job_update(job: jobs.Job) -> dict:
    p = job.params
    return system.apply(p.get("branch"), backup=p.get("backup", True), log=job.log, step=job.step)


def _job_backup(job: jobs.Job) -> dict:
    p = job.params
    result = backup.create(p.get("type", "auto"), label=p.get("label", ""), log=job.log, step=job.step)
    if remote.config()["enabled"]:
        # A failing remote copy must not turn a good local backup into a failed job:
        # report it in the result and as its own event (alerts listen to remote.failed).
        bid = result["backup"]["id"]
        try:
            r = remote.sync(log=job.log, step=job.step)
            result["remote"] = {"ok": True, **r}
            events.emit("remote.succeeded", True, r["message"], log=job.log, backup=bid)
        except CtlError as e:
            job.log(f"Remote copy failed: {e}")
            job.step(f"Remote copy failed: {e}", "remote_failed", error=str(e))
            result["remote"] = {"ok": False, "error": str(e)}
            result["message"] += "; remote copy failed"
            events.emit("remote.failed", False, str(e), log=job.log, backup=bid)
    return result


def _job_restore(job: jobs.Job) -> dict:
    p = job.params
    fn = remote.restore_from_remote if p.get("remote") else backup.restore
    return fn(p["id"], components=p.get("components"), safety_backup=p.get("safety_backup", True),
              log=job.log, step=job.step)


def _job_remote_sync(job: jobs.Job) -> dict:
    return remote.sync(log=job.log, step=job.step)


jobs.HANDLERS.update(update=_job_update, backup=_job_backup, restore=_job_restore, remote_sync=_job_remote_sync)


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

    rm = sub.add_parser("remote", help="copies of the backups on an FTP, FTPS or SCP server").add_subparsers(
        dest="action", required=True)
    sh = rm.add_parser("show", help="settings (without secrets)")
    sh.add_argument("--reveal", action="store_true", help="also show the encryption passphrase")
    st = rm.add_parser("set", help="change settings: remote set protocol=scp host=backup.example.org ...")
    st.add_argument("set", nargs="*", metavar="KEY=VALUE")
    st.add_argument("--stdin", action="store_true", help="read the settings as JSON from stdin (for secrets)")
    rm.add_parser("test", help="test the connection (stores the SSH host key on first use)")
    rm.add_parser("forget-hostkey", help="forget the stored SSH host key")
    rm.add_parser("list", help="backups on the remote location")
    sy = rm.add_parser("sync", help="upload missing backups now and remove old ones remotely")
    sy.add_argument("--wait", action="store_true")

    ev = sub.add_parser("events", help="recent system events (for alerts)")
    ev.add_argument("--limit", type=int, default=50)

    rs = sub.add_parser("restore", help="restore a backup (makes a safety backup first)")
    rs.add_argument("id")
    rs.add_argument("--only", help="comma separated: files,database,redis")
    rs.add_argument("--no-safety-backup", action="store_true")
    rs.add_argument("--from-remote", action="store_true", help="download it from the remote location first")
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
    if a.cmd == "remote":
        return _remote(a)
    if a.cmd == "events":
        return {"events": events.recent(max(1, min(a.limit, 500)))}
    if a.cmd == "restore":
        comps = [c.strip() for c in a.only.split(",")] if a.only else None
        if a.from_remote:
            if not backup.BACKUP_ID.match(a.id):
                raise CtlError(f"Not a valid backup id: {a.id}")
        else:
            backup._resolve(a.id)  # fail fast on a wrong id, before starting a job
        params = {"id": a.id, "components": comps, "safety_backup": not a.no_safety_backup}
        if a.from_remote:
            params["remote"] = True
        return _launch("restore", params, a.wait)
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


def _remote(a) -> object:
    if a.action == "show":
        return remote.show(reveal=a.reveal)
    if a.action == "set":
        changes = {}
        if a.stdin:
            try:
                changes = json.loads(sys.stdin.read() or "{}")
            except ValueError as e:
                raise CtlError("Settings on stdin must be JSON") from e
            if not isinstance(changes, dict):
                raise CtlError("Settings on stdin must be a JSON object")
        for kv in a.set:
            if "=" not in kv:
                raise CtlError(f"Use KEY=VALUE, not {kv}")
            k, v = kv.split("=", 1)
            changes[k] = v.lower() in ("1", "true", "yes", "on") if k in ("enabled", "verify_tls", "encrypt") else v
        unknown = set(changes) - set(remote.DEFAULT) - {"password", "passphrase"}
        if unknown:
            raise CtlError(f"Unknown remote setting: {', '.join(sorted(unknown))}")
        return remote.set_config(changes)
    if a.action == "test":
        return remote.test(log=lambda *_: None)
    if a.action == "forget-hostkey":
        return remote.forget_host_key()
    if a.action == "list":
        return {"backups": remote.list_remote()}
    if a.action == "sync":
        if not remote.config()["enabled"]:
            raise CtlError("The remote location is switched off")
        return _launch("remote_sync", {}, a.wait)
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
