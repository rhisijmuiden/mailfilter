"""Branding: own name, colours and logos (admin), read by every page (public).

The "Powered by RHIS" mark is not a setting and is not served from here; it is
part of the app itself (see LICENSE, additional terms).
"""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import branding
from ..audit import audit
from ..deps import DB, AdminUser

router = APIRouter(prefix="/branding", tags=["branding"])
# No login: the login page and the public pages (report links, link
# protection) use the same branding.
public = APIRouter(prefix="/branding", tags=["branding"])

Kind = Literal["logo", "logo_dark", "favicon"]


@public.get("")
def get_branding(db: DB) -> dict:
    return branding.public(db)


@public.get("/{kind}")
def get_image(kind: Kind, db: DB) -> Response:
    img = branding.get_image(db, kind)
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    ctype, data = img
    return Response(data, media_type=ctype, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        # An uploaded SVG opened on its own must not run scripts.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    })


class BrandingIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    primary: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


@router.put("")
def save(body: BrandingIn, request: Request, user: AdminUser, db: DB) -> dict:
    try:
        branding.save(db, name=body.name, accent=body.accent, primary=body.primary)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit(db, request, user, "branding.save", body.name, accent=body.accent, primary=body.primary)
    db.commit()
    return branding.public(db)


class ImageIn(BaseModel):
    data: str = Field(min_length=1, max_length=branding.MAX_IMAGE * 2)  # base64


@router.put("/{kind}")
def put_image(kind: Kind, body: ImageIn, request: Request, user: AdminUser, db: DB) -> dict:
    raw = body.data.split(",", 1)[1] if body.data.startswith("data:") else body.data
    try:
        data = base64.b64decode(raw, validate=True)
        ctype = branding.put_image(db, kind, data)
    except (binascii.Error, ValueError) as exc:
        code = str(exc) if str(exc) in ("too_large", "bad_image") else "bad_image"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, code) from exc
    audit(db, request, user, "branding.image_set", kind, type=ctype, size=len(data))
    db.commit()
    return branding.public(db)


@router.delete("/{kind}")
def delete_image(kind: Kind, request: Request, user: AdminUser, db: DB) -> dict:
    branding.delete_image(db, kind)
    audit(db, request, user, "branding.image_removed", kind)
    db.commit()
    return branding.public(db)


@router.post("/reset")
def reset(request: Request, user: AdminUser, db: DB) -> dict:
    branding.reset(db)
    audit(db, request, user, "branding.reset")
    db.commit()
    return branding.public(db)
