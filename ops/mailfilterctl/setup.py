"""First-time setup wizard in the web interface (mostly for the Proxmox LXC template).

The template's first boot asks only for the admin login, its password and the
web port (the IP address comes from Proxmox). It then leaves the marker file
``setup-wizard-pending`` in /etc/mailfilter, and the web interface walks the
admin through the rest. Only the server name and the admin e-mail address
need root: ``setup server`` re-runs the installer with them as a background
job. ``setup finish`` removes the marker.
"""
import os
import re
import subprocess

from . import paths
from .util import CtlError, run
from .web import _env

MARKER = "setup-wizard-pending"
FQDN = re.compile(r"^(?=.{4,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
EMAIL = re.compile(r"^[^@\s\"'<>]{1,64}@[a-z0-9.-]+\.[a-z]{2,}$")


def container() -> str:
    """'lxc', 'docker', ... or '' on a real or virtual machine."""
    try:
        p = subprocess.run(["systemd-detect-virt", "--container"], capture_output=True, text=True, timeout=5)
        name = p.stdout.strip()
        return "" if p.returncode != 0 or name == "none" else name
    except (OSError, subprocess.TimeoutExpired):
        return ""


def show() -> dict:
    e = _env()
    return {
        "pending": (paths.etc() / MARKER).exists(),
        "hostname": e.get("MF_HOSTNAME", ""),
        "admin_email": e.get("MF_ADMIN_EMAIL", ""),
        "ipv4": e.get("MF_PUBLIC_V4", ""),
        "ipv6": e.get("MF_PUBLIC_V6", ""),
        "web_url": e.get("MF_WEB_URL", ""),
        "web_mode": e.get("MF_WEB_MODE", "public"),
        "container": container(),
    }


def validate(params: dict) -> dict:
    host = str(params.get("hostname") or "").strip().lower().rstrip(".")
    email = str(params.get("admin_email") or "").strip().lower()
    if not FQDN.match(host):
        raise CtlError(f"Use a full host name such as mailfilter01.example.nl, not '{host}'")
    if host.endswith((".localdomain", ".local", ".localhost")):
        raise CtlError("Use the real host name that the internet will see, not a .local name")
    if not EMAIL.match(email):
        raise CtlError(f"Not a valid e-mail address: {email}")
    return {"hostname": host, "admin_email": email}


def apply(params: dict, log=print, step=None) -> dict:
    """Re-runs the installer with the new name and address (Postfix, Caddy, certificates, DNS hints)."""
    p = validate(params)
    if step:
        step(f"Setting the server name to {p['hostname']}", "setup_server")
    args = ["--reconfigure", "--hostname", p["hostname"], "--admin-email", p["admin_email"]]
    if os.environ.get("MF_SETUP_CMD"):
        cmd = os.environ["MF_SETUP_CMD"].split() + args
    else:
        cmd = ["bash", str(paths.source() / "install" / "install.sh"), *args]
    run(cmd, timeout=3600, log=log)
    return {"message": f"Server name: {p['hostname']}", **show()}


def finish() -> dict:
    """Closes the wizard. The admin chose an own password in it, so the first one
    (shown on the console of the Proxmox template) is removed from disk and screen."""
    for name in (MARKER, "initial-admin-password", "initial-admin-login"):
        try:
            (paths.etc() / name).unlink()
        except FileNotFoundError:
            pass
    issue = paths.source() / "appliance" / "issue.sh"
    if (paths.etc() / "appliance").exists() and issue.exists():
        run(["bash", str(issue)], check=False, timeout=30)
    return {"message": "Setup finished", **show()}
