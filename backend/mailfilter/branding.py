"""Own branding: product name, colours and logos, set by an admin in the web UI.

Mailfilter is open source; every install may look like its own product. The
default is the RHIS house style (rhis-huisstijl). One element is fixed and is
NOT part of these settings: the small "Powered by RHIS" mark at the bottom
right of every page (see LICENSE, additional terms under AGPLv3 section 7(b)).
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from sqlalchemy.orm import Session

from .settings_store import get_setting, set_setting

KEY = "branding"
IMAGE_KINDS = ("logo", "logo_dark", "favicon")
IMAGE_KEY = {k: f"branding.{k}" for k in IMAGE_KINDS}
MAX_IMAGE = 256 * 1024
IMAGE_TYPES = {"image/svg+xml", "image/png", "image/webp", "image/jpeg", "image/x-icon", "image/vnd.microsoft.icon"}

# RHIS house style
DEFAULTS: dict[str, Any] = {"name": "Mailfilter", "accent": "#FF6B57", "primary": "#14213D"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def get(db: Session) -> dict[str, Any]:
    raw = get_setting(db, KEY) or {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if raw.get(k):
            out[k] = raw[k]
    return out


def save(db: Session, *, name: str, accent: str, primary: str) -> dict[str, Any]:
    name = " ".join(name.split())[:40] or DEFAULTS["name"]
    for v in (accent, primary):
        if not HEX.match(v):
            raise ValueError("bad_color")
    set_setting(db, KEY, {"name": name, "accent": accent.upper(), "primary": primary.upper()})
    return get(db)


def sniff(data: bytes) -> str | None:
    """The image type from the bytes themselves, never from what the browser said."""
    head = data[:512].lstrip()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or head.startswith(b"<!--"):
        if b"<svg" in data[:4096]:
            return "image/svg+xml"
    return None


def put_image(db: Session, kind: str, data: bytes) -> str:
    if kind not in IMAGE_KINDS:
        raise ValueError("bad_kind")
    if not data or len(data) > MAX_IMAGE:
        raise ValueError("too_large")
    ctype = sniff(data)
    if ctype is None:
        raise ValueError("bad_image")
    set_setting(db, IMAGE_KEY[kind], {"type": ctype, "data": base64.b64encode(data).decode()})
    return ctype


def get_image(db: Session, kind: str) -> tuple[str, bytes] | None:
    row = get_setting(db, IMAGE_KEY.get(kind, ""))
    if not row:
        return None
    return row["type"], base64.b64decode(row["data"])


def delete_image(db: Session, kind: str) -> None:
    set_setting(db, IMAGE_KEY[kind], None)


def on_color(hex_color: str) -> str:
    """Dark or white text, whichever reads best on this background colour."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#14213D" if lum > 0.179 else "#FFFFFF"


def public(db: Session) -> dict[str, Any]:
    """What every page (also the login page) needs. Image URLs carry a hash so
    browsers can cache them for a long time."""
    out = get(db)
    for kind in IMAGE_KINDS:
        row = get_setting(db, IMAGE_KEY[kind])
        out[kind] = (f"/api/branding/{kind}?v={hashlib.sha256(row['data'].encode()).hexdigest()[:12]}"
                     if row else None)
    out["custom"] = out["accent"] != DEFAULTS["accent"] or out["primary"] != DEFAULTS["primary"] or any(
        out[k] for k in IMAGE_KINDS) or out["name"] != DEFAULTS["name"]
    return out


def reset(db: Session) -> None:
    set_setting(db, KEY, None)
    for kind in IMAGE_KINDS:
        delete_image(db, kind)


# The fixed "Powered by RHIS" mark for pages the server renders itself (link
# protection). The web app has the same mark in components/PoweredBy.tsx.
# Not a setting: see LICENSE, additional terms.
RHIS_ICON = ('<svg viewBox="0 0 64 64" width="16" height="16" aria-hidden="true"><rect width="64" height="64" rx="16" fill="#14213D"/>'
             '<rect x="27.5" y="31" width="9" height="22" rx="2.5" fill="#FF6B57"/><path d="M25.64 22.64A9 9 0 0 1 38.36 22.64'
             'M20.69 17.69A16 16 0 0 1 43.31 17.69" fill="none" stroke="#FF6B57" stroke-width="4.5" stroke-linecap="round"/></svg>')
POWERED_BY_CSS = (".powered-by{position:fixed;right:max(10px,env(safe-area-inset-right,0px));bottom:max(8px,env(safe-area-inset-bottom,0px));"
                  "display:inline-flex;align-items:center;gap:6px;font:500 11.5px/1 Archivo,system-ui,sans-serif;color:var(--muted);"
                  "text-decoration:none;opacity:.85;padding:4px 6px;border-radius:6px}.powered-by:hover{opacity:1}"
                  ".powered-by svg{flex:none}.powered-by b{font-weight:700}")
POWERED_BY_HTML = (f'<a class="powered-by" href="https://rhis.nl" target="_blank" rel="noopener">{RHIS_ICON}'
                   '<span>Powered by <b>RHIS</b></span></a>')


def current() -> dict[str, Any]:
    """Branding for code that has no database session at hand; defaults when the
    database cannot be read."""
    from .db import session
    try:
        with session() as db:
            return public(db)
    except Exception:  # noqa: BLE001 - a page must still render
        return {**DEFAULTS, "logo": None, "logo_dark": None, "favicon": None, "custom": False}
