"""Talks to Postfix's queue through the small root helper backend/bin/mf-queuectl
(allowed via sudoers), and to Rspamd's controller for learning spam/ham."""

from __future__ import annotations

import json
import re
import subprocess

import httpx

from .settings import get_settings

QUEUE_ID = re.compile(r"^[0-9A-Za-z]{5,20}$")
MAX_MESSAGE = 30 * 1024 * 1024


class QueueError(RuntimeError):
    pass


def _helper(*args: str, timeout: int = 30) -> bytes:
    cmd = [*get_settings().queue_helper, *args]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise QueueError(f"helper_failed:{e}") from e
    if res.returncode != 0:
        raise QueueError(res.stderr.decode(errors="replace").strip() or "helper_failed")
    return res.stdout


def check_id(queue_id: str) -> str:
    if not QUEUE_ID.match(queue_id):
        raise QueueError("bad_queue_id")
    return queue_id


def held_ids() -> set[str]:
    """Queue IDs currently in the hold queue (= quarantine)."""
    out = _helper("list-hold")
    ids = set()
    for line in out.decode().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line)["queue_id"])
        except (ValueError, KeyError):
            continue
    return ids


def summary() -> dict[str, int]:
    """Messages per Postfix queue, e.g. {"active": 2, "deferred": 14, "hold": 3}."""
    data = json.loads(_helper("summary").decode() or "{}")
    return {str(k): int(v) for k, v in data.items()}


def raw_message(queue_id: str) -> bytes:
    data = _helper("show", check_id(queue_id), timeout=60)
    return data[:MAX_MESSAGE]


def release(queue_id: str) -> None:
    _helper("release", check_id(queue_id))


def delete(queue_id: str) -> None:
    _helper("delete", check_id(queue_id))


def learn(kind: str, message: bytes) -> bool:
    """Teach Rspamd's Bayes filter. kind is "spam" or "ham". Returns False when
    Rspamd already learned this message or is not reachable (non-fatal)."""
    s = get_settings()
    try:
        r = httpx.post(
            f"{s.rspamd_controller}/learn{kind}",
            content=message,
            headers={"Password": s.rspamd_password},
            timeout=30,
        )
    except httpx.HTTPError:
        return False
    return r.status_code == 200
