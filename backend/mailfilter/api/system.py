"""Info button, update check, update and switching between production and test.

The work is done by mailfilterctl (see backend/mailfilter/ctl.py); this module
only checks who may do what and records it in the audit log.
"""

from __future__ import annotations

import os
import time
from pathlib import Path as PathType
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import __version__, ctl
from ..audit import audit
from ..deps import COOKIE, DB, AdminUser, CurrentUser
from ..models import AuthSession, User
from ..security import MIN_PASSWORD_LENGTH, hash_password, token_hash
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
    web_mode: str = "public"  # System > Web access; pages warn when features need public HTTPS
    setup_pending: bool = False  # first-time setup wizard not finished yet (Proxmox template)


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
        web_mode=get_settings().web_mode,
        setup_pending=_setup_marker().exists(),
    )


def _setup_marker() -> PathType:
    # Read at every call (not the import-time ETC), so tests and a finished wizard show up right away.
    return PathType(os.environ.get("MF_ETC", str(ETC))) / "setup-wizard-pending"


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


# ---- first-time setup wizard (Proxmox template: only admin, password and port were asked) ----
class SetupServerIn(BaseModel):
    hostname: str = Field(min_length=4, max_length=253)
    admin_email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    # The template's first boot makes the login "admin" with a password shown on the console;
    # the wizard turns it into the admin's own e-mail address and password.
    new_password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=500)


@router.get("/setup")
def setup_state(_: AdminUser) -> dict[str, Any]:
    return ctl.call("setup", "show")


@router.put("/setup/server")
def setup_server(body: SetupServerIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    """Re-runs the installer with the real server name as a job (Postfix, Caddy, DNS hints).

    The e-mail address also becomes the admin's login, and a new password replaces the
    one that the console showed."""
    email = body.admin_email.strip().lower()
    taken = db.execute(select(User).where(func.lower(User.email) == email, User.id != user.id)).scalar_one_or_none()
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, "email_exists")
    job = ctl.call("setup", "server", "--stdin", input=body.model_dump_json(exclude={"new_password"}))
    if user.email != email:
        audit(db, request, user, "user_updated", email, previous=user.email, via="setup")
        user.email = email
    if body.new_password:
        user.password_hash = hash_password(body.new_password)
        keep = token_hash(request.cookies.get(COOKIE, ""))
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != keep))
        audit(db, request, user, "password_changed", email, via="setup")
    audit(db, request, user, "system.setup_server", body.hostname, job=job.get("id"), admin_email=body.admin_email)
    db.commit()
    return job


@router.post("/setup/finish")
def setup_finish(request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    # Runs as a job outside this service's sandbox (it changes /etc); it takes a second or two.
    job = ctl.call("setup", "finish")
    for _ in range(60):
        if job.get("state") not in ("queued", "running"):
            break
        time.sleep(0.5)
        job = ctl.call("job", "status", job["id"])
    if job.get("state") != "done":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, job.get("message") or "setup finish failed")
    out = ctl.call("setup", "show")
    audit(db, request, user, "system.setup_finished", "")
    db.commit()
    return out
