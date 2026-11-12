"""Admin: import a Proxmox Mail Gateway backup.

Flow: upload the pmg-backup_*.tgz -> preview (what is new, what already
exists, what cannot be taken over, problems) -> tick items -> apply.

Applying has two parts:

* Domains, address routes, relay networks and TLS policies go into the gateway
  tables in the database (the source of truth for Domains and Relay access)
  and are applied through mailfilter.gateway.apply_changes, like a save on
  those pages: validated, written by the root helper, rolled back on failure.
* Allow/block lists and DKIM keys are files only root may write, and the web
  app's sandbox keeps /etc read-only. So it leaves that selection in the
  staging directory and starts the root job ``mailfilter-pmg-import.service``,
  which writes them and a result file (see mailfilter_pmg.staging).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailfilter_pmg import BackupError, read_backup
from mailfilter_pmg.importer import GATEWAY_SECTIONS, SECTIONS, SERVER_SECTIONS, merge_gateway, net_key, preview
from mailfilter_pmg.staging import paths

from .. import gateway
from ..audit import audit
from ..deps import DB, AdminUser
from ..models import AddressRoute, Domain, DomainDestination, DomainRoute, RelayNetwork, TlsPolicy

router = APIRouter(prefix="/pmg-import", tags=["pmg-import"])

MAX_UPLOAD = 512 * 1024 * 1024


def staging_dir() -> str:
    return os.environ.get("MF_PMG_STAGING", "/var/lib/mailfilter/pmg-import")


def job_command() -> list[str]:
    cmd = os.environ.get("MF_PMG_IMPORT_CMD")
    return cmd.split() if cmd else ["sudo", "-n", "/usr/bin/systemctl", "start", "mailfilter-pmg-import.service"]


def _paths(import_id: str) -> dict[str, str]:
    try:
        p = paths(import_id, staging_dir())
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if not os.path.isfile(p["backup"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return p


def _plan(import_id: str):
    try:
        return read_backup(_paths(import_id)["backup"])
    except BackupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"pmg_bad_backup:{exc}")


def _view(import_id: str, db: Session) -> dict:
    plan = _plan(import_id)
    gateway.ensure_imported(db)
    pv = preview(plan, gateway.build_config(db))
    data = plan.to_dict()
    return {
        "id": import_id,
        "source": {"product": plan.source_product, "version": plan.source_version, "backup_time": plan.backup_time},
        "summary": plan.summary(),
        "items": pv.to_dict()["items"],
        "problems": pv.problems,
        "rules": data["rules"],
        "warnings": data["warnings"],
        "settings": data["settings"],
    }


@router.post("", status_code=201)
async def upload(request: Request, admin: AdminUser, db: DB) -> dict:
    """The backup file is the raw request body (Content-Type: application/octet-stream)."""
    os.makedirs(staging_dir(), mode=0o750, exist_ok=True)
    import_id = secrets.token_hex(16)
    p = paths(import_id, staging_dir())
    size = 0
    fd = os.open(p["backup"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "pmg_too_large")
                fh.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "pmg_empty")
        view = _view(import_id, db)
    except BaseException:
        _remove(import_id)
        raise
    audit(db, request, admin, "pmg_import.upload", import_id, size=size, **view["source"])
    db.commit()
    return view


@router.get("/{import_id}")
def get_import(import_id: str, _: AdminUser, db: DB) -> dict:
    return _view(import_id, db)


class ApplyIn(BaseModel):
    selection: dict[str, list[str]]


def _save_gateway(db: Session, picked: dict[str, list]) -> None:
    """Write the domains, routes, networks and TLS policies taken over from PMG into the gateway tables."""
    for d in picked["domains"]:
        dom = db.execute(select(Domain).where(func.lower(Domain.name) == d.name)).scalar_one_or_none()
        if dom is None:
            dom = Domain(name=d.name)
            db.add(dom)
            db.flush()
        route = db.get(DomainRoute, dom.id)
        if route is None:
            route = DomainRoute(domain_id=dom.id, tls=d.tls, verify_recipients=d.verify_recipients, note=d.note)
            db.add(route)
        elif not route.note:
            route.note = d.note
        route.destinations = [DomainDestination(domain_id=dom.id, position=i, host=x.host, port=x.port, mx=x.mx,
                                                protocol=x.protocol) for i, x in enumerate(d.destinations)]
    nets = {net_key(n.cidr): n for n in db.execute(select(RelayNetwork)).scalars()}
    for n in picked["relay_networks"]:
        row = nets.get(net_key(n.cidr))
        if row is None:
            db.add(RelayNetwork(cidr=n.cidr, description=n.description[:200], enabled=True, allow_broad=n.allow_broad))
        else:
            row.enabled = True
    for a in picked["address_routes"]:
        row = db.execute(select(AddressRoute).where(func.lower(AddressRoute.address) == a.address.lower())).scalar_one_or_none()
        if row is None:
            row = AddressRoute(address=a.address)
            db.add(row)
        x = a.destination
        row.host, row.port, row.mx, row.protocol, row.note = x.host, x.port, x.mx, x.protocol, a.note
    for t in picked["tls_policies"]:
        row = db.execute(select(TlsPolicy).where(func.lower(TlsPolicy.destination) == t.destination.lower())).scalar_one_or_none()
        if row is None:
            row = TlsPolicy(destination=t.destination)
            db.add(row)
        row.level, row.note = t.level, t.note


def _run_server_job(p: dict[str, str], selection: dict[str, list[str]]) -> dict:
    try:
        os.unlink(p["result"])
    except FileNotFoundError:
        pass
    with open(p["selection"], "w", encoding="utf-8") as fh:
        json.dump(selection, fh)
    open(p["request"], "w").close()
    try:
        run = subprocess.run(job_command(), capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"import job did not start: {exc}"}
    try:
        with open(p["result"], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "error": (run.stderr or run.stdout or "import job gave no result").strip()[-500:]}


@router.post("/{import_id}/apply")
def apply(import_id: str, body: ApplyIn, request: Request, admin: AdminUser, db: DB) -> dict:
    p = _paths(import_id)
    plan = _plan(import_id)
    selection = {k: sorted(set(v)) for k, v in body.selection.items() if k in SECTIONS and v}
    if not selection:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pmg_nothing_selected")
    result: dict = {"ok": True, "imported": {}, "problems": []}

    # 1. Gateway settings: database + apply, exactly like a save on the Domains / Relay access pages
    if any(selection.get(s) for s in GATEWAY_SECTIONS):
        gateway.ensure_imported(db)
        _, picked, problems = merge_gateway(plan, gateway.build_config(db), selection)
        if problems:
            return {"ok": False, "imported": {}, "problems": problems}
        _save_gateway(db, picked)
        try:
            gateway.apply_changes(db)  # rolls back and raises when the server refuses the change
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": str(exc.detail)}
            return {"ok": False, "imported": {}, "problems": detail.get("problems", []),
                    "error": detail.get("output") or detail.get("code", "")}
        db.commit()
        result["imported"].update({k: len(v) for k, v in picked.items()})

    # 2. Allow/block lists and DKIM keys: the root job
    server = {s: selection[s] for s in SERVER_SECTIONS if selection.get(s)}
    if server:
        job = _run_server_job(p, server)
        result["imported"].update(job.get("imported", {}))
        if not job.get("ok"):
            result["ok"] = False
            result["problems"] += job.get("problems", [])
            result["error"] = job.get("error", "")

    audit(db, request, admin, "pmg_import.apply", import_id, ok=result["ok"], imported=result["imported"])
    db.commit()
    return result


def _remove(import_id: str) -> None:
    for path in paths(import_id, staging_dir()).values():
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@router.delete("/{import_id}", status_code=204)
def delete(import_id: str, _: AdminUser) -> None:
    _paths(import_id)
    _remove(import_id)
