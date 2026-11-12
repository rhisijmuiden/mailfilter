"""Local backups: list, create (full or incremental), settings, verify, restore, delete."""

from __future__ import annotations

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
    job = ctl.call("backup", "create", "--type", body.type, "--label", body.label or f"by {user.email}")
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


@router.post("/{backup_id}/restore")
def restore(backup_id: BackupId, body: RestoreIn, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    args = ["restore", backup_id, "--only", ",".join(body.components or ["files", "database", "redis"])]
    if not body.safety_backup:
        args.append("--no-safety-backup")
    # Audit first: the restore may replace this database a moment later.
    audit(db, request, user, "backup.restore", backup_id, components=body.components, safety_backup=body.safety_backup)
    db.commit()
    return ctl.call(*args)


@router.delete("/chains/{chain_id}")
def delete_chain(chain_id: ChainId, request: Request, db: DB, user: AdminUser) -> dict[str, Any]:
    result = ctl.call("backup", "delete", chain_id)
    audit(db, request, user, "backup.delete", chain_id)
    db.commit()
    return result
