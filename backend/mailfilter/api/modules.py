"""System > Modules: switch optional modules on and off (see mailfilter.modules)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select

from mailfilter_config.rules import effective

from .. import modules
from ..audit import audit
from ..cluster import state as cluster_state
from ..deps import DB, AdminUser, CurrentUser
from ..gateway import apply_changes, custom_rules
from ..models import ApiKey, ArchiveDomain, CloudTenant, MailTrap

router = APIRouter(prefix="/modules", tags=["modules"])


class ModuleOut(BaseModel):
    key: str
    enabled: bool
    default: bool
    gateway: bool
    per_node: bool
    in_use: int  # how many things are set up in it (0: switching off changes nothing for mail)


class ModulesOut(BaseModel):
    enabled: dict[str, bool]
    modules: list[ModuleOut] | None = None  # admins only
    cluster_member: bool = False


class SwitchIn(BaseModel):
    enabled: bool


def _in_use(db, key: str) -> int:
    count = lambda q: db.execute(select(func.count()).select_from(q)).scalar_one()  # noqa: E731
    if key == "cloud":
        return count(select(CloudTenant).where(CloudTenant.enabled.is_(True)).subquery())
    if key == "archive":
        return count(select(ArchiveDomain).where(ArchiveDomain.enabled.is_(True)).subquery())
    if key == "api":
        return count(select(ApiKey).subquery())
    if key == "mailtrap":
        return count(select(MailTrap).subquery())
    if key == "link_protection":
        rule = effective(custom_rules(db))["link_protection"]
        return int(rule.enabled) + sum(1 for v in rule.domains.values() if v)
    if key == "cluster":
        return int(cluster_state.enabled(db))
    return 0


def _out(db, user) -> ModulesOut:
    on = modules.states(db)
    if not user.is_admin:
        return ModulesOut(enabled=on)
    return ModulesOut(
        enabled=on, cluster_member=cluster_state.enabled(db),
        modules=[ModuleOut(key=m.key, enabled=on[m.key], default=m.default, gateway=m.gateway, per_node=m.per_node,
                           in_use=_in_use(db, m.key)) for m in modules.MODULES],
    )


@router.get("", response_model=ModulesOut)
def get_modules(user: CurrentUser, db: DB) -> ModulesOut:
    return _out(db, user)


@router.put("/{key}", response_model=ModulesOut)
def switch(key: str, body: SwitchIn, request: Request, admin: AdminUser, db: DB) -> ModulesOut:
    m = modules.BY_KEY.get(key)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown_module")
    if key == "cluster" and not body.enabled and cluster_state.enabled(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "cluster_member")  # leave the cluster first
    if modules.enabled(db, key) != body.enabled:
        modules.set_enabled(db, key, body.enabled)
        if m.gateway:
            apply_changes(db)  # rolls back and raises when the gateway refuses
        audit(db, request, admin, "module_switched", key, enabled=body.enabled)
        db.commit()
    return _out(db, admin)
