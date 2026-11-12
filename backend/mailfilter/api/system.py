"""Info button, update check, update and switching between production and test.

The work is done by mailfilterctl (see backend/mailfilter/ctl.py); this module
only checks who may do what and records it in the audit log.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request
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


JobId = Annotated[str, Path(pattern=r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9]{6}$")]


@router.get("/jobs/{job_id}")
def job(job_id: JobId, _: AdminUser) -> dict[str, Any]:
    return ctl.call("job", "status", job_id)


@router.get("/jobs/{job_id}/log")
def job_log(job_id: JobId, _: AdminUser) -> dict[str, Any]:
    return ctl.call("job", "log", job_id)
