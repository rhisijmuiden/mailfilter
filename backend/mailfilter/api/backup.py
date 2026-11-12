"""Backups: list, create (full or incremental), settings, verify, restore, delete,
and copies on a remote FTP, FTPS or SCP server."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field

from .. import ctl
from ..audit import audit
from ..deps import DB, AdminUser

router = APIRouter(prefix="/backups", tags=["backups"])

BackupId = Annotated[str, Path(pattern=r"^[0-9]{8}T[0-9]{6}Z\.[0-9]{3}$")]
ChainId = Annotated[str, Path(pattern=r"^[0-9]{8}T[0-9]{6}Z$")]


@router.get("")
def list_backups(_: AdminUser) -> dict[str, Any]:
    return ctl.call("backup", "list")


class CreateIn(BaseModel):
    type: Literal["auto", "full", "incremental"] = "auto"
    label: str = Field(default="", max_length=100)


@router.post("")
def create(body: CreateIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    job = ctl.call("backup", "create", "--type", body.type, f"--label={body.label or f'by {user.email}'}")
    audit(db, request, user, "backup.create", body.type, job=job.get("id"))
    db.commit()
    return job


class ConfigIn(BaseModel):
    enabled: bool
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    full_every_days: int = Field(ge=1, le=90)
    keep_full: int = Field(ge=1, le=100)


@router.put("/config")
def set_config(body: ConfigIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    pairs = [f"enabled={'true' if body.enabled else 'false'}", f"time={body.time}",
             f"full_every_days={body.full_every_days}", f"keep_full={body.keep_full}"]
    cfg = ctl.call("backup", "config", *pairs)
    audit(db, request, user, "backup.config", "", **body.model_dump())
    db.commit()
    return cfg


@router.post("/{backup_id}/verify")
def verify(backup_id: BackupId, _: AdminUser) -> dict[str, Any]:
    return ctl.call("backup", "verify", backup_id, timeout=900)


class RestoreIn(BaseModel):
    components: list[Literal["files", "database", "redis"]] = ["files", "database", "redis"]
    safety_backup: bool = True
    remote: bool = False  # download it from the remote location first


@router.post("/{backup_id}/restore")
def restore(backup_id: BackupId, body: RestoreIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    args = ["restore", backup_id, "--only", ",".join(body.components or ["files", "database", "redis"])]
    if not body.safety_backup:
        args.append("--no-safety-backup")
    if body.remote:
        args.append("--from-remote")
    # Audit first: the restore may replace this database a moment later.
    audit(db, request, user, "backup.restore", backup_id, components=body.components, safety_backup=body.safety_backup,
          remote=body.remote)
    db.commit()
    return ctl.call(*args)


# ---------------------------------------------------------------- remote location

@router.get("/remote")
def remote_show(_: AdminUser) -> dict[str, Any]:
    return ctl.call("remote", "show")


@router.get("/remote/passphrase")
def remote_passphrase(request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    data = ctl.call("remote", "show", "--reveal")
    audit(db, request, user, "backup.remote.passphrase_shown", "")
    db.commit()
    return {"passphrase": data.get("passphrase", "")}


class RemoteIn(BaseModel):
    enabled: bool
    protocol: Literal["ftp", "ftps", "scp"]
    host: str = Field(default="", max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    path: str = Field(default="mailfilter-backups", max_length=255)
    verify_tls: bool = True
    encrypt: bool = True
    keep_full: int = Field(default=4, ge=1, le=100)
    # None keeps the stored value, "" clears it
    password: str | None = Field(default=None, max_length=256)
    passphrase: str | None = Field(default=None, max_length=256)


@router.put("/remote")
def remote_set(body: RemoteIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    data = body.model_dump()
    if data["port"] is None:
        data["port"] = ""
    result = ctl.call("remote", "set", "--stdin", input=json.dumps(data))
    audit(db, request, user, "backup.remote.config", body.host,
          **{k: v for k, v in data.items() if k not in ("password", "passphrase")},
          password_changed=body.password is not None, passphrase_changed=body.passphrase is not None)
    db.commit()
    return result


@router.post("/remote/test")
def remote_test(_: AdminUser) -> dict[str, Any]:
    return ctl.call("remote", "test", timeout=300)


@router.post("/remote/forget-hostkey")
def remote_forget(request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    result = ctl.call("remote", "forget-hostkey")
    audit(db, request, user, "backup.remote.forget_hostkey", "")
    db.commit()
    return result


@router.get("/remote/backups")
def remote_list(_: AdminUser) -> dict[str, Any]:
    return ctl.call("remote", "list", timeout=300)


@router.post("/remote/sync")
def remote_sync(request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    job = ctl.call("remote", "sync")
    audit(db, request, user, "backup.remote.sync", "", job=job.get("id"))
    db.commit()
    return job


@router.delete("/chains/{chain_id}")
def delete_chain(chain_id: ChainId, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    result = ctl.call("backup", "delete", chain_id)
    audit(db, request, user, "backup.delete", chain_id)
    db.commit()
    return result
