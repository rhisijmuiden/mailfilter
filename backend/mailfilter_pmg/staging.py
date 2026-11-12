"""Hand-over between the web app (user ``mailfilter``) and the root import job.

The web app cannot write to /etc (its systemd unit has ProtectSystem=full,
which also holds for anything it starts through sudo). So it drops a request
in the staging directory and asks systemd to run ``mailfilter-pmg-import.service``
(a root oneshot outside that sandbox), which processes every pending request
and writes a result file next to it.

Per import id ``ID`` (32 hex characters) the directory holds:

* ``ID.tgz``             the uploaded backup (written by the web app)
* ``ID.selection.json``  ``{section: [item keys]}`` (written by the web app)
* ``ID.request``         marker: please apply (written by the web app)
* ``ID.result.json``     outcome (written by root)

The directory belongs to the web user, so root treats everything in it as
untrusted: it only looks at names matching the id pattern, never follows
symlinks when reading, and writes results with an atomic rename so a planted
symlink is replaced instead of followed.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
from dataclasses import asdict

STAGING_DIR = "/var/lib/mailfilter/pmg-import"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_SELECTION_BYTES = 4 * 1024 * 1024


def paths(import_id: str, staging_dir: str = STAGING_DIR) -> dict[str, str]:
    if not ID_RE.match(import_id or ""):
        raise ValueError("invalid import id")
    base = os.path.join(staging_dir, import_id)
    return {
        "backup": base + ".tgz",
        "selection": base + ".selection.json",
        "request": base + ".request",
        "result": base + ".result.json",
    }


def read_nofollow(path: str, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{os.path.basename(path)} is too large")
    return data


def write_result(path: str, data: dict, gid: int | None = None) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".result-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(tmp, 0o640)
        if gid is not None and os.geteuid() == 0:
            os.chown(tmp, 0, gid)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def pending(staging_dir: str = STAGING_DIR) -> list[str]:
    try:
        names = os.listdir(staging_dir)
    except FileNotFoundError:
        return []
    return sorted(n[: -len(".request")] for n in names if n.endswith(".request") and ID_RE.match(n[: -len(".request")]))


def run_pending(staging_dir: str = STAGING_DIR, root: str = "", gateway_json: bool = False, applier_factory=None) -> list[str]:
    """Apply every pending request (allow/block lists and DKIM keys). Returns the processed ids.

    Domains, routes, relay networks and TLS policies are saved by the web app
    in its gateway tables and applied through mailfilter.gateway, so they are
    ignored here unless ``gateway_json`` asks to merge them into gateway.json.
    """
    from .backup import MAX_ARCHIVE_BYTES, BackupError, read_backup
    from mailfilter_config.apply import Applier, ApplyError

    from .importer import GATEWAY_SECTIONS, apply_gateway_file, apply_server_files

    try:
        gid = os.stat(staging_dir).st_gid
    except FileNotFoundError:
        return []
    done = []
    for import_id in pending(staging_dir):
        p = paths(import_id, staging_dir)
        try:
            os.unlink(p["request"])
        except FileNotFoundError:
            continue
        try:
            plan = read_backup(io.BytesIO(read_nofollow(p["backup"], MAX_ARCHIVE_BYTES)))
            selection = json.loads(read_nofollow(p["selection"], MAX_SELECTION_BYTES))
            if not isinstance(selection, dict) or not all(
                isinstance(v, list) and all(isinstance(x, str) for x in v) for v in selection.values()
            ):
                raise ValueError("selection must map sections to lists of keys")
            res = None
            if gateway_json and any(selection.get(x) for x in GATEWAY_SECTIONS):
                applier = applier_factory() if applier_factory else Applier(root=root)
                res = apply_gateway_file(plan, selection, root=root, applier=applier)
            if res is None or res.ok:  # lists and DKIM only after the gateway part went through
                files = apply_server_files(plan, selection, root=root)
                if res is not None:
                    files.imported.update(res.imported)
                    files.changed_files = res.changed_files + files.changed_files
                res = files
            result = asdict(res)
        except ApplyError as exc:
            result = {"ok": False, "error": str(exc), "step": exc.step, "output": exc.output}
        except (BackupError, OSError, ValueError) as exc:
            result = {"ok": False, "error": str(exc)}
        write_result(p["result"], result, gid)
        done.append(import_id)
    return done
