"""Version information, update check, update and switching between production and test."""
import os
import platform
import re
from pathlib import Path

from . import paths
from .util import CtlError, have, iso, now, read_json, run, write_json

BRANCHES = {"main": "production", "test": "test"}
COMPONENT_PACKAGES = ["postfix", "rspamd", "clamav-daemon", "postgresql", "redis-server", "dovecot-core",
                      "caddy", "crowdsec", "nftables"]
# A branch can only be installed when it already contains this updater; older
# commits cannot update themselves from the web interface.
REQUIRED_FILE = "ops/bin/mailfilterctl"


def _git(*args, timeout=120, check=True, log=None):
    return run(["git", "-c", f"safe.directory={paths.source()}", "-C", paths.source(), *args],
               timeout=timeout, check=check, log=log)


def _read_version(text: str | None) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else "0.0.0"


def version_info() -> dict:
    """Version of the running checkout. Cheap: no network."""
    src = paths.source()
    info = {"version": _read_version((src / "VERSION").read_text() if (src / "VERSION").exists() else None)}
    if (src / ".git").exists() and have("git"):
        p = _git("log", "-1", "--format=%H%x1f%cI%x1f%s", check=False)
        if p.returncode == 0 and p.stdout.strip():
            commit, date, subject = p.stdout.strip().split("\x1f", 2)
            info.update(commit=commit, commit_short=commit[:7], commit_date=date, commit_subject=subject)
        b = _git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        info["branch"] = b.stdout.strip() if b.returncode == 0 else None
    else:
        etc = paths.etc()
        info["branch"] = (etc / "branch").read_text().strip() if (etc / "branch").exists() else None
    info["channel"] = BRANCHES.get(info.get("branch") or "", "custom")
    label = info["version"]
    if info["channel"] != "production" and info.get("commit_short"):
        label += f"-{info.get('branch')}+{info['commit_short']}"
    info["label"] = label
    return info


def _package_versions() -> dict:
    if not have("dpkg-query"):
        return {}
    p = run(["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${db:Status-Abbrev}\n", *COMPONENT_PACKAGES],
            check=False)
    out = {}
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2].startswith("ii"):
            out[parts[0]] = parts[1]
    return out


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return platform.platform()


def info() -> dict:
    """Everything the Info screen shows."""
    data = version_info()
    data.update(
        hostname=platform.node(),
        os=_os_name(),
        kernel=platform.release(),
        python=platform.python_version(),
        components=_package_versions(),
        last_check=read_json(paths.state() / "update-check.json"),
        last_update=(read_json(paths.state() / "update-history.json", []) or [None])[-1],
        repository=_git("remote", "get-url", "origin", check=False).stdout.strip()
        if (paths.source() / ".git").exists() else None,
    )
    try:
        with open("/proc/uptime") as f:
            data["uptime_seconds"] = int(float(f.read().split()[0]))
    except OSError:
        pass
    return data


def _remote_branch_info(branch: str) -> dict | None:
    ref = f"origin/{branch}"
    p = _git("log", "-1", "--format=%H%x1f%cI", ref, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    commit, date = p.stdout.strip().split("\x1f")
    v = _git("show", f"{ref}:VERSION", check=False)
    installable = _git("cat-file", "-e", f"{ref}:{REQUIRED_FILE}", check=False).returncode == 0
    return {"branch": branch, "channel": BRANCHES[branch], "commit": commit, "commit_short": commit[:7],
            "commit_date": date, "version": _read_version(v.stdout if v.returncode == 0 else None),
            "installable": installable}


def check(log=None) -> dict:
    """Fetch from GitHub and report what an update or branch switch would bring."""
    current = version_info()
    branches = list(BRANCHES)
    _git("fetch", "--quiet", "--prune", "origin", *[f"+refs/heads/{b}:refs/remotes/origin/{b}" for b in branches],
         timeout=120, check=False, log=log)
    available = {b: _remote_branch_info(b) for b in branches}
    branch = current.get("branch")
    result = {"checked_at": iso(now()), "current": current, "branches": available,
              "update_available": False, "behind": 0, "commits": []}
    if branch in BRANCHES and available.get(branch):
        behind = _git("rev-list", "--count", f"HEAD..origin/{branch}", check=False)
        result["behind"] = int(behind.stdout.strip() or 0) if behind.returncode == 0 else 0
        result["update_available"] = result["behind"] > 0
        lg = _git("log", "-n", "50", "--format=%h%x1f%cI%x1f%s", f"HEAD..origin/{branch}", check=False)
        result["commits"] = [dict(zip(("commit", "date", "subject"), line.split("\x1f", 2)))
                             for line in lg.stdout.splitlines() if line]
    write_json(paths.state() / "update-check.json", result)
    return result


def reconfigure(log) -> None:
    """Re-apply the installer with the saved settings, without asking anything."""
    installer = paths.source() / "install" / "install.sh"
    if os.environ.get("MF_RECONFIGURE_CMD"):
        cmd = os.environ["MF_RECONFIGURE_CMD"].split()
    else:
        cmd = ["bash", installer, "--reconfigure"]
    run(cmd, timeout=3600, log=log)


def apply(branch: str | None = None, backup: bool = True, log=print, step=None) -> dict:
    """Update to the newest commit of a branch (the current one by default).

    Makes a backup first, then moves the checkout and re-applies the
    installer. If that fails, it puts the previous version back.
    """
    from . import backup as backup_mod  # local import: backup imports this module

    step = step or (lambda text, *_, **__: log(text))
    current = version_info()
    branch = branch or current.get("branch")
    if branch not in BRANCHES:
        raise CtlError(f"Choose the production (main) or test branch, not '{branch}'")
    step(f"Downloading the newest {BRANCHES[branch]} version", "update_download", channel=BRANCHES[branch])
    _git("fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", log=log)
    target = _remote_branch_info(branch)
    if not target:
        raise CtlError(f"Branch {branch} does not exist on GitHub")
    if not target["installable"]:
        raise CtlError(f"The {BRANCHES[branch]} branch has no release that supports updating from the web "
                       "interface yet. Nothing was changed.")
    if target["commit"] == current.get("commit") and branch == current.get("branch"):
        return {"message": f"Already up to date ({current['label']})", "changed": False}

    dirty = _git("status", "--porcelain", "--untracked-files=no", check=False).stdout.strip()
    if dirty:
        log("Local changes in the checkout will be overwritten:\n" + dirty)

    if backup:
        step("Making a backup before updating", "update_backup")
        backup_mod.create("auto", label=f"before update to {target['version']} ({branch})", log=log)

    previous = {"branch": current.get("branch"), "commit": current.get("commit")}
    step(f"Installing {target['version']} ({branch} {target['commit_short']})", "update_install",
         version=target["version"], branch=branch, commit=target["commit_short"])
    _git("checkout", "--quiet", "--force", "-B", branch, f"origin/{branch}", log=log)
    _git("branch", "--quiet", f"--set-upstream-to=origin/{branch}", branch, check=False)
    try:
        step("Applying configuration and restarting services", "update_apply")
        reconfigure(log)
    except CtlError as e:
        step("Update failed, putting the previous version back", "update_rollback")
        if previous["commit"]:
            _git("checkout", "--quiet", "--force", "-B", previous["branch"] or branch, previous["commit"], log=log)
            try:
                reconfigure(log)
            except CtlError as e2:
                log(f"Rollback could not re-apply the configuration either: {e2}")
        _history(previous, target, ok=False, error=str(e))
        raise CtlError(f"Update failed and was rolled back: {e}") from e

    new = version_info()
    _history(previous, target, ok=True)
    try:
        check()
    except CtlError:
        pass
    return {"message": f"Updated to {new['label']}", "changed": True, "from": previous, "to": new}


def _history(previous: dict, target: dict, ok: bool, error: str = "") -> None:
    path = paths.state() / "update-history.json"
    hist = read_json(path, []) or []
    hist.append({"at": iso(now()), "from": previous, "to": {k: target[k] for k in ("branch", "commit", "version")},
                 "ok": ok, "error": error})
    write_json(path, hist[-50:])


_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def validate_version_file(text: str) -> bool:
    return bool(_SEMVER.match(_read_version(text)))
