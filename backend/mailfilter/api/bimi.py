"""BIMI per domain: logo (SVG Tiny PS), optional mark certificate (VMC/CMC),
the DNS record and the DMARC prerequisite. Admin only, except the two public
files that mailbox providers fetch.

The logic lives in ``mailfilter_dns.bimi``; the record is published through
the DNS toolbox (``/api/dns/publish`` with kind ``bimi``).
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from mailfilter_dns import bimi
from mailfilter_dns.resolver import FreshTxt, norm

from ..audit import audit
from ..deps import DB, AdminUser
from ..models.core import Domain
from . import dns as dns_api

router = APIRouter(prefix="/bimi", tags=["bimi"])
# No login: Gmail, Apple, Yahoo fetch the logo and certificate from here
public = APIRouter(prefix="/bimi", tags=["bimi"])


def _domain(db, name: str) -> str:
    d = dns_api._domain(name)
    if db.scalar(select(Domain.id).where(Domain.name == d)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown_domain")
    return d


def _status(d: str) -> dict:
    server = dns_api.get_server()
    return bimi.status(d, server.hostname, FreshTxt(dns_api.get_resolver(), d))


@router.get("/domains")
def domains(_: AdminUser, db: DB) -> list[dict]:
    """Every domain with a short BIMI state, for the overview list."""
    out = []
    for name in db.scalars(select(Domain.name).order_by(Domain.name)):
        out.append({"domain": name, "logo": bimi.read_file(name, "svg") is not None,
                    "cert": bimi.read_file(name, "pem") is not None})
    return out


@router.get("/status/{domain}")
def get_status(domain: str, _: AdminUser, db: DB) -> dict:
    return _status(_domain(db, domain))


class SvgIn(BaseModel):
    svg: str = Field(min_length=1, max_length=bimi.MAX_SVG * 8)
    convert: bool = False
    title: str = Field(default="", max_length=100)


def _svg(body: SvgIn, d: str) -> tuple[bytes, list[str], str | None]:
    data = body.svg.encode()
    if not body.convert:
        return data, [], None
    try:
        out, changes = bimi.convert_svg(data, body.title or d)
    except ValueError as exc:
        return data, [], str(exc)
    return out, changes, None


@router.post("/logo/{domain}/check")
def logo_check(domain: str, body: SvgIn, _: AdminUser, db: DB) -> dict:
    """Check a logo (and optionally convert it) without saving, for the preview."""
    d = _domain(db, domain)
    data, changes, error = _svg(body, d)
    rep = bimi.check_svg(data)
    return {"report": rep.to_dict(), "changes": changes, "convert_error": error,
            "svg": data.decode("utf-8", "replace") if body.convert and not error else None}


@router.put("/logo/{domain}")
def logo_save(domain: str, body: SvgIn, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(db, domain)
    data, changes, error = _svg(body, d)
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error)
    rep = bimi.check_svg(data)
    if not rep.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": "svg_invalid", "problems": rep.errors})
    bimi.write_file(d, "svg", data)
    audit(db, request, user, "bimi.logo_set", d, sha256=rep.sha256[:16], converted=bool(changes))
    db.commit()
    return _status(d)


@router.delete("/logo/{domain}")
def logo_remove(domain: str, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(db, domain)
    bimi.write_file(d, "svg", None)
    audit(db, request, user, "bimi.logo_removed", d)
    db.commit()
    return _status(d)


class CertIn(BaseModel):
    pem: str = Field(min_length=1, max_length=bimi.MAX_PEM)


@router.post("/cert/{domain}/check")
def cert_check(domain: str, body: CertIn, _: AdminUser, db: DB) -> dict:
    d = _domain(db, domain)
    return bimi.check_cert(body.pem.encode(), d, bimi.read_file(d, "svg")).to_dict()


@router.put("/cert/{domain}")
def cert_save(domain: str, body: CertIn, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(db, domain)
    pem = body.pem.encode()
    rep = bimi.check_cert(pem, d, bimi.read_file(d, "svg"))
    if not rep.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": "cert_invalid", "problems": rep.errors})
    bimi.write_file(d, "pem", pem)
    audit(db, request, user, "bimi.cert_set", d, kind=rep.kind, not_after=rep.not_after)
    db.commit()
    return _status(d)


@router.delete("/cert/{domain}")
def cert_remove(domain: str, request: Request, user: AdminUser, db: DB) -> dict:
    d = _domain(db, domain)
    bimi.write_file(d, "pem", None)
    audit(db, request, user, "bimi.cert_removed", d)
    db.commit()
    return _status(d)


@router.post("/logo/{domain}/from-cert")
def logo_from_cert(domain: str, request: Request, user: AdminUser, db: DB) -> dict:
    """Use the logo that is inside the certificate (it must be identical anyway)."""
    d = _domain(db, domain)
    pem = bimi.read_file(d, "pem")
    logo = bimi.logo_from_cert(pem) if pem else None
    if logo is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_logo_in_cert")
    rep = bimi.check_svg(logo)
    if not rep.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": "svg_invalid", "problems": rep.errors})
    bimi.write_file(d, "svg", logo)
    audit(db, request, user, "bimi.logo_from_cert", d, sha256=rep.sha256[:16])
    db.commit()
    return _status(d)


@router.get("/preview/{domain}")
def preview(domain: str, _: AdminUser, db: DB) -> dict:
    """The stored logo as a data URL, for an <img> in the interface."""
    d = _domain(db, domain)
    logo = bimi.read_file(d, "svg")
    return {"data_url": "data:image/svg+xml;base64," + base64.b64encode(logo).decode() if logo else None}


@public.get("/{name}", include_in_schema=False)
def public_file(name: str) -> Response:
    """``https://<hostname>/api/bimi/<domain>.svg`` and ``.pem``."""
    domain, dot, ext = name.rpartition(".")
    if not dot or ext not in ("svg", "pem"):
        return Response("not found\n", status_code=404, media_type="text/plain")
    data = bimi.read_file(norm(domain), ext)
    if data is None:
        return Response("not found\n", status_code=404, media_type="text/plain")
    media = "image/svg+xml" if ext == "svg" else "application/pem-certificate-chain"
    return Response(data, media_type=media, headers={
        "Cache-Control": "public, max-age=3600",
        # The logo is shown as an image only; never let a browser run it as a page
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "X-Content-Type-Options": "nosniff",
    })
