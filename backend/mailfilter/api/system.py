"""Version information for the Info button. Updates and branch switching are
added here by the updates feature."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from ..deps import CurrentUser
from ..settings import ETC, get_settings


router = APIRouter(prefix="/system", tags=["system"])


class InfoOut(BaseModel):
    version: str
    app_version: str
    branch: str
    hostname: str


def _read(name: str, default: str) -> str:
    try:
        return (ETC / name).read_text().strip() or default
    except OSError:
        return default


@router.get("/info", response_model=InfoOut)
def info(_: CurrentUser) -> InfoOut:
    return InfoOut(
        version=_read("version", __version__),
        app_version=__version__,
        branch=_read("branch", "test"),
        hostname=get_settings().hostname,
    )
