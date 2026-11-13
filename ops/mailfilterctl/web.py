"""How the web interface is reached (System > Web access).

``web show`` reads the settings from mailfilter.env; ``web set`` checks the new
settings and runs install/web-access.sh as a background job, because it
restarts Caddy and the web app that asked for it.
"""
import ipaddress
import os
import re

from . import paths
from .util import CtlError, run

MODES = ("public", "internal", "proxy", "http")
RESERVED_PORTS = {25, 465, 587, 993, 8000, 11332, 11333, 11334}
_URL = re.compile(r"^https?://[^/\s\"']+(/[^\s\"']*)?$")


def _env() -> dict:
    out = {}
    try:
        text = (paths.etc() / "mailfilter.env").read_text()
    except OSError:
        return out
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _nets(value) -> list[str]:
    items = value.split() if isinstance(value, str) else list(value or [])
    out = []
    for n in items:
        n = str(n).strip().replace(",", "")
        if not n or n == "-":
            continue
        try:
            out.append(str(ipaddress.ip_network(n, strict=False)) if "/" in n else str(ipaddress.ip_address(n)))
        except ValueError:
            raise CtlError(f"Not a valid IP address or network: {n}") from None
    if len(out) > 50:
        raise CtlError("At most 50 networks")
    return out


def show() -> dict:
    e = _env()
    mode = e.get("MF_WEB_MODE") or "public"
    host = e.get("MF_HOSTNAME", "")
    return {
        "mode": mode if mode in MODES else "public",
        "port": int(e.get("MF_WEB_PORT") or {"proxy": 8080, "http": 80}.get(mode, 443)),
        "url": e.get("MF_WEB_URL") or f"https://{host}",
        "trusted_proxies": e.get("MF_TRUSTED_PROXIES", "").split(),
        "web_from": e.get("MF_WEB_FROM", "").split(),
        "hostname": host,
        "own_certificate": (paths.etc() / "web-cert.pem").exists(),
        "letsencrypt": os.path.exists(f"/etc/letsencrypt/live/{host}/fullchain.pem") if host else False,
    }


def validate(params: dict) -> dict:
    mode = params.get("mode")
    if mode not in MODES:
        raise CtlError(f"Unknown mode: {mode}")
    out: dict = {"mode": mode}
    if mode in ("proxy", "http"):
        try:
            port = int(params.get("port") or (8080 if mode == "proxy" else 80))
        except (TypeError, ValueError):
            raise CtlError("Not a valid port") from None
        if not 1 <= port <= 65535 or port in RESERVED_PORTS:
            raise CtlError(f"Port {port} cannot be used for the web interface")
        out["port"] = port
    if mode == "proxy":
        proxies = _nets(params.get("trusted_proxies"))
        if not proxies:
            raise CtlError("Give the address of the reverse proxy")
        out["trusted_proxies"] = proxies
        url = str(params.get("url") or "").strip().rstrip("/")
        if url and not _URL.match(url):
            raise CtlError(f"Not a valid web address: {url}")
        out["url"] = url
    if mode in ("internal", "http"):
        out["web_from"] = _nets(params.get("web_from"))
    return out


def command(p: dict) -> list[str]:
    cmd = ["bash", str(paths.source() / "install" / "web-access.sh"), "--mode", p["mode"]]
    if "port" in p:
        cmd += ["--web-port", str(p["port"])]
    if p.get("url"):
        cmd += ["--web-url", p["url"]]
    if "trusted_proxies" in p:
        for n in p["trusted_proxies"] or ["-"]:
            cmd += ["--trusted-proxy", n]
    if "web_from" in p:
        for n in p["web_from"] or ["-"]:
            cmd += ["--web-from", n]
    return cmd


def apply(params: dict, log=print, step=None) -> dict:
    p = validate(params)
    if step:
        step(f"Switching the web interface to {p['mode']}", "web_access")
    cmd = os.environ["MF_WEB_ACCESS_CMD"].split() + command(p)[2:] if os.environ.get("MF_WEB_ACCESS_CMD") else command(p)
    run(cmd, timeout=900, log=log)
    return {"message": f"Web interface: {p['mode']}", **show()}
