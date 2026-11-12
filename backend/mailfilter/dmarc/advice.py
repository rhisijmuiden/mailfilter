"""The next safe step towards p=reject, worked out from the reports.

The road is none -> quarantine -> reject. A step is only advised when the
reports show that every real sender (Mailfilter itself, known mail services
and senders that mostly pass) is aligned; senders that always fail and are
unknown are most likely forgeries, which is exactly what the policy blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_DAYS_NONE = 7  # days of reports at p=none before quarantine
MIN_DAYS_QUARANTINE = 14  # days at quarantine before reject
FAIL_SHARE = 0.02  # a real sender may fail this share (forwarding, mailing lists)
FAIL_MIN = 3  # ... or this many messages


@dataclass
class Group:
    name: str
    kind: str  # own, known, host, unknown
    messages: int = 0
    passed: int = 0
    dkim_pass: int = 0
    spf_pass: int = 0
    forwarded: int = 0  # failures the reporter marked as forwarded / mailing list
    ips: dict = field(default_factory=dict)

    @property
    def failed(self) -> int:
        return self.messages - self.passed

    @property
    def legit(self) -> bool:
        return self.kind in ("own", "known") or (self.messages > 0 and self.passed / self.messages >= 0.5)

    @property
    def blocking(self) -> bool:
        fails = self.failed - self.forwarded
        return self.legit and fails > max(FAIL_MIN, FAIL_SHARE * self.messages)

    @property
    def verdict(self) -> str:
        if self.failed == 0:
            return "ok"
        if self.blocking:
            return "fix"
        if self.legit:
            return "minor"  # a few failures, usually forwarding
        return "suspect"  # unknown and failing: probably forged


def advise(*, tags: dict | None, record_error: bool, has_our_rua: bool, messages: int, days: int,
           days_at_policy: int, groups: list[Group], can_publish: bool) -> dict:
    """Returns {"code", "level", "params", "action"}; action is a publish kind
    for the DNS toolbox ({"kind": "dmarc_rua"} or {"kind": "dmarc_policy", "policy": ...}) or None."""

    def out(code, level, action=None, **params):
        return {"code": code, "level": level, "params": params,
                "action": action if (action and can_publish) else None}

    if record_error:
        return out("record_invalid", "bad")
    if tags is None:
        return out("no_record", "bad", {"kind": "dmarc_rua"})
    if not has_our_rua:
        return out("no_rua", "warn", {"kind": "dmarc_rua"})
    if messages == 0:
        return out("waiting_reports", "info")
    p = tags.get("p", "none").lower()
    pct = tags.get("pct", "100")
    blockers = [g.name for g in groups if g.blocking]
    if blockers:
        return out("fix_senders", "warn", senders=", ".join(blockers[:5]), count=len(blockers))
    if p == "none":
        if days < MIN_DAYS_NONE:
            return out("collecting", "info", days=days, need=MIN_DAYS_NONE)
        return out("ready_quarantine", "ok", {"kind": "dmarc_policy", "policy": "quarantine"})
    if pct != "100":
        return out("raise_pct", "warn", {"kind": "dmarc_policy", "policy": p}, pct=pct, policy=p)
    if p == "quarantine":
        if days_at_policy < MIN_DAYS_QUARANTINE:
            return out("quarantine_wait", "info", days=days_at_policy, need=MIN_DAYS_QUARANTINE)
        return out("ready_reject", "ok", {"kind": "dmarc_policy", "policy": "reject"})
    sp = tags.get("sp", "").lower()
    if sp in ("none", "quarantine"):
        return out("done_sp", "ok", sp=sp)
    return out("done", "ok")
