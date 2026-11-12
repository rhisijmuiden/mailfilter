"""The public REST API: /api/v1, authenticated with API keys.

It serves the same endpoints as the web app for the areas in
mailfilter.apikeys.AREAS (domains, relay IPs, SMTP accounts, quarantine,
trace, allow/block lists, customers, audit log), so a hosting panel can
automate them. Everything else under /api/v1 answers 403 not_available_in_api.
The OpenAPI description at /api/v1/openapi.json lists only what a key can use.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import BaseModel

from .. import __version__
from ..apikeys import AREAS, OPEN_PATHS, PREFIX, RATE_LIMIT, api_key_of, area_for
from ..deps import CurrentUser
from . import audit, gateway, lists, quarantine, trace, users

FEATURES = (gateway.router, quarantine.router, trace.router, lists.router, users.router, audit.router)
key_router = APIRouter()
router = APIRouter(prefix=PREFIX)


class MeOut(BaseModel):
    key_name: str
    key_hint: str
    owner: str
    role: str
    domains: list[str] | None  # None: all domains (admin key)
    scopes: dict[str, str]
    allowed_ips: list[str]
    expires_at: datetime | None
    rate_limit_per_minute: int


@key_router.get("/me", response_model=MeOut, tags=["key"], summary="The key you are using")
def me(request: Request, user: CurrentUser) -> MeOut:
    key = api_key_of(request)
    assert key is not None
    return MeOut(key_name=key.name, key_hint=key.hint, owner=user.email, role=user.role,
                 domains=None if user.is_admin else user.domain_names, scopes=key.scopes or {},
                 allowed_ips=list(key.allowed_ips or []), expires_at=key.expires_at, rate_limit_per_minute=RATE_LIMIT)


_PARAM = re.compile(r"\{[^}]+\}")
_HIDDEN = {"/api/v1/users/domain-options", "/api/v1/lists/options"}
_spec: dict[str, Any] | None = None


def _public_routes() -> list[APIRoute]:
    """Routes a key can use, with paths relative to /api/v1."""
    out = []
    for feature in (key_router, *FEATURES):
        for r in feature.routes:
            if not isinstance(r, APIRoute) or not r.include_in_schema:
                continue
            if r.path in OPEN_PATHS or area_for(_PARAM.sub("1", r.path)) is not None:
                out.append(r)
    return out


def openapi_spec() -> dict[str, Any]:
    global _spec
    if _spec is None:
        routes = _public_routes()
        spec = get_openapi(
            title="RHIS Mailfilter API", version=__version__, routes=routes,
            description=(
                "Automate Mailfilter from a hosting panel or script. Send your API key as "
                "`Authorization: Bearer mfk_...`. A key acts as its owner (admin: all domains, "
                "customer: own domains) and only for its scopes. Read scopes allow GET, write scopes "
                f"allow every method. At most {RATE_LIMIT} requests per key per minute (429 with Retry-After)."
            ),
        )
        spec["paths"] = {PREFIX + p: ops for p, ops in spec["paths"].items()}
        spec["servers"] = [{"url": "/"}]
        spec.setdefault("components", {})["securitySchemes"] = {"apiKey": {"type": "http", "scheme": "bearer"}}
        spec["security"] = [{"apiKey": []}]
        spec["tags"] = [{"name": "key"}] + [{"name": a.key} for a in AREAS]
        for path, ops in spec["paths"].items():
            area = area_for(_PARAM.sub("1", path))
            for method, op in ops.items():
                if area is not None:
                    op["tags"] = [area.key]
                    level = "read" if method in ("get", "head") else "write"
                    op["x-scope"] = f"{area.key}:{level}"
                    if area.admin_only:
                        op["x-admin-only"] = True
                if path in _HIDDEN:
                    op["x-helper"] = True
        _spec = spec
    return _spec


@router.get("/openapi.json", include_in_schema=False)
def openapi() -> dict[str, Any]:
    return openapi_spec()


router.include_router(key_router)
for _feature in FEATURES:
    router.include_router(_feature)
