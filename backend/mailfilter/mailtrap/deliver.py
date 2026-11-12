"""mailfilter-trap-deliver: the command behind Postfix's mftrap pipe (master.cf).

    mailfilter-trap-deliver SLUG --client=IP --sasl=LOGIN --sender=ADDR -- RECIPIENT...

Stores the message in the Mailtrap mailbox SLUG. In "all except testers" mode,
recipients on the mailbox's allow list also get the mail for real (sent again
through the local Postfix). Exit codes follow sysexits: 0 = stored, 75 = try
again later (Postfix keeps the mail queued, it is never sent on).
"""

from __future__ import annotations

import argparse
import smtplib
import socket
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

from . import checks, store

EX_TEMPFAIL = 75
EX_USAGE = 64
MAX_BYTES = 60 * 1024 * 1024


def allowed(address: str, allow: list[str]) -> bool:
    a = address.lower()
    return any(a == x or (x.startswith("@") and a.endswith(x)) for x in allow)


def send_on(raw: bytes, sender: str, recipients: list[str]) -> None:
    """Hand the original message to the local Postfix (127.0.0.1, so it is relayed
    normally, DKIM-signed and checked) for these recipients only."""
    with smtplib.SMTP("127.0.0.1", 25, local_hostname=socket.getfqdn(), timeout=60) as s:
        refused = s.sendmail(sender or "", recipients, raw)
    if refused:
        raise RuntimeError(f"refused: {', '.join(refused)}")


def deliver(slug: str, raw: bytes, *, client: str = "", sasl: str = "", sender: str = "",
            recipients: list[str], config: dict | None = None, sendmail=send_on) -> str:
    cfg = config if config is not None else store.load_config()
    box = (cfg.get("boxes") or {}).get(slug)
    if not box:
        raise LookupError(f"unknown Mailtrap mailbox {slug!r}")
    original = store.strip_our_headers(raw)
    delivered: list[str] = []
    if box.get("mode") == "except" and box.get("allow"):
        delivered = [r for r in recipients if allowed(r, box["allow"])]
        if delivered:
            sendmail(original, sender, delivered)
    nl = store.line_ending(original)
    msg = BytesParser(policy=policy.default).parsebytes(
        store.envelope_headers(slug, sender, recipients, client, sasl, [], "", nl) + original)
    summary = checks.summary(checks.run(msg, len(original)))  # type: ignore[arg-type]
    data = store.envelope_headers(slug, sender, recipients, client, sasl, delivered, summary, nl) + original
    maildir = Path(box["maildir"])
    msg_id = store.write_message(maildir, data)
    store.cleanup(maildir, int(box.get("retention_days", 7)), int(box.get("max_messages", 1000)))
    return msg_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mailfilter-trap-deliver")
    ap.add_argument("slug")
    ap.add_argument("--client", default="")
    ap.add_argument("--sasl", default="")
    ap.add_argument("--sender", default="")
    ap.add_argument("recipients", nargs="+")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return EX_USAGE
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        print("message too large for the Mailtrap", file=sys.stderr)
        return 0  # dropped on purpose: retrying will not make it smaller, and it must never be sent
    try:
        deliver(args.slug, raw, client=args.client, sasl=args.sasl, sender=args.sender, recipients=args.recipients)
    except Exception as e:  # anything else: keep it queued and try again later
        print(f"Mailtrap: {e}", file=sys.stderr)
        return EX_TEMPFAIL
    return 0


if __name__ == "__main__":
    sys.exit(main())
