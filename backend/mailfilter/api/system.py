"""Info button, update check, update and switching between production and test.

The work is done by mailfilterctl (see backend/mailfilter/ctl.py); this module
only checks who may do what and records it in the audit log.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel

from .. import __version__, ctl
from ..audit import audit
from ..deps import DB, AdminUser, CurrentUser
from ..settings import ETC, get_settings

router = APIRouter(prefix="/system", tags=["system"])


class InfoOut(BaseModel):
    version: str
    app_version: str
    branch: str
    hostname: str
    label: str = ""
    channel: str = ""
    commit: str = ""
    commit_date: str = ""


def _read(name: str, default: str) -> str:
    try:
        return (ETC / name).read_text().strip() or default
    except OSError:
        return default


@router.get("/info", response_model=InfoOut)
def info(_: CurrentUser) -> InfoOut:
    """Cheap version info for everyone who is logged in (no root helper needed)."""
    version = _read("version", __version__)
    branch = _read("branch", "test")
    return InfoOut(
        version=version,
        app_version=__version__,
        branch=branch,
        hostname=get_settings().hostname,
        label=version,
        channel={"main": "production", "test": "test"}.get(branch, "custom"),
    )


@router.get("/details")
def details(_: AdminUser) -> dict[str, Any]:
    """Everything mailfilterctl knows: commit, OS, component versions, last check and update."""
    return ctl.call("info")


@router.post("/update/check")
def check(_: AdminUser) -> dict[str, Any]:
    return ctl.call("update", "check", timeout=180)


class UpdateIn(BaseModel):
    branch: Literal["main", "test"] | None = None
    backup: bool = True


@router.post("/update")
def update(body: UpdateIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    args = ["update", "apply"]
    if body.branch:
        args += ["--branch", body.branch]
    if not body.backup:
        args.append("--no-backup")
    job = ctl.call(*args)
    audit(db, request, user, "system.update", body.branch or "", job=job.get("id"), backup=body.backup)
    db.commit()
    return job


@router.get("/jobs")
def jobs(_: AdminUser) -> dict[str, Any]:
    return ctl.call("job", "list")


@router.get("/events")
def system_events(_: AdminUser, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Recent system events (backups, remote copies, restores, updates), newest first."""
    return ctl.call("events", "--limit", str(limit))


JobId = Annotated[str, Path(pattern=r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9]{6}$")]


@router.get("/jobs/{job_id}")
def job(job_id: JobId, _: AdminUser) -> dict[str, Any]:
    return ctl.call("job", "status", job_id)


@router.get("/jobs/{job_id}/log")
def job_log(job_id: JobId, _: AdminUser) -> dict[str, Any]:
    return ctl.call("job", "log", job_id)


# ---- web access: how the web interface is reached (System > Web access) ---------------
class WebAccessIn(BaseModel):
    mode: Literal["public", "internal", "proxy", "http"]
    port: int | None = None
    url: str = ""
    trusted_proxies: list[str] = []
    web_from: list[str] = []


@router.get("/web")
def web_access(_: AdminUser) -> dict[str, Any]:
    return ctl.call("web", "show")


@router.put("/web")
def set_web_access(body: WebAccessIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    """Starts install/web-access.sh as a job: Caddy and the web app restart, maybe on another address."""
    job = ctl.call("web", "set", "--stdin", input=body.model_dump_json())
    audit(db, request, user, "system.web_access", body.mode, job=job.get("id"), port=body.port,
          trusted_proxies=body.trusted_proxies, web_from=body.web_from, url=body.url)
    db.commit()
    return job
