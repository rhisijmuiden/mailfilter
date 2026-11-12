"""Talks to mailfilterctl, the root-side helper for updates and backups.

The web app runs as the unprivileged ``mailfilter`` user and calls
``sudo -n /usr/local/sbin/mailfilterctl --json ...``. Long tasks come back as a
job id; the job keeps running under systemd even if this web app restarts.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from fastapi import HTTPException, status


def command() -> list[str]:
    override = os.environ.get("MF_CTL_COMMAND")
    return override.split() if override else ["sudo", "-n", "/usr/local/sbin/mailfilterctl"]


def call(*args: str, timeout: int = 180, input: str | None = None) -> Any:
    """Run mailfilterctl and return its JSON answer.

    Its own errors (``{"error": ...}``) become HTTP 400 with detail
    ``ctl_error:<message>``; a missing or broken helper becomes 503.
    ``input`` goes to stdin, so secrets never appear in the process list.
    """
    try:
        p = subprocess.run([*command(), "--json", *args], capture_output=True, text=True, timeout=timeout,
                           input=input)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ctl_unavailable") from None
    except subprocess.TimeoutExpired:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "ctl_timeout") from None
    try:
        data = json.loads(p.stdout or "null")
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "error" in data and p.returncode != 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"ctl_error:{data['error']}")
    if data is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ctl_unavailable")
    return data
