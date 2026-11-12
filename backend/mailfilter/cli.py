"""mailfilter-admin: command line tasks for the installer and for emergencies.

  mailfilter-admin migrate                     bring the database schema up to date
  mailfilter-admin create-admin EMAIL          create an admin (prints a generated password)
  mailfilter-admin set-password EMAIL          set a new generated password
  mailfilter-admin reset-2fa EMAIL             turn off two-factor login (lost phone and codes)
  mailfilter-admin has-users                   exit 0 when at least one user exists
  mailfilter-admin abuse-scan                  pause senders that suddenly send spam (run by a timer)
  mailfilter-admin notify-check                send alerts by e-mail, Telegram and webhook (run by a timer)
  mailfilter-admin write-lists                 rewrite the allow/block list maps for Rspamd
  mailfilter-admin quarantine-report [--now]   send the daily quarantine reports when due
  mailfilter-admin monthly-report [--now]      measure usage, store last month's numbers, send the monthly reports when due
  mailfilter-admin cluster-sync                exchange configuration with the other cluster nodes (timer)
  mailfilter-admin cluster-leave               take this server out of its cluster (keeps its configuration)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select


def _alembic_config():
    from alembic.config import Config

    return Config(str(Path(__file__).with_name("alembic.ini")))


def migrate() -> None:
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def main(argv: list[str] | None = None) -> int:
    from .cluster import tracking
    from .db import session
    from .models import User
    from .security import generate_password, hash_password

    tracking.install()  # changes made here (e.g. set-password) reach the other nodes
    p = argparse.ArgumentParser(prog="mailfilter-admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    c = sub.add_parser("create-admin")
    c.add_argument("email")
    c.add_argument("--name", default="Admin")
    c.add_argument("--language", choices=["en", "nl"], default="en")
    s = sub.add_parser("set-password")
    s.add_argument("email")
    r = sub.add_parser("reset-2fa")
    r.add_argument("email")
    sub.add_parser("has-users")
    sub.add_parser("abuse-scan")
    sub.add_parser("notify-check")
    sub.add_parser("write-lists")
    sub.add_parser("gateway-apply", help="write the gateway configuration again from the database")
    qr = sub.add_parser("quarantine-report")
    qr.add_argument("--now", action="store_true", help="send now, even when not due")
    mr = sub.add_parser("monthly-report")
    mr.add_argument("--now", action="store_true", help="send last month's reports now, even when not due")
    sub.add_parser("dkim-rotate", help="start due DKIM key rotations and move running ones forward")
    sub.add_parser("cluster-sync")
    sub.add_parser("cluster-leave")
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        migrate()
        return 0
    with session() as db:
        if args.cmd == "abuse-scan":
            from .abuse import scan

            for h in scan(db):
                print(f"paused {h.kind} {h.value}: {h.reason}")
            return 0
        if args.cmd == "notify-check":
            from .notify import run_checks

            for a in run_checks(db):
                print(f"{a.event} {a.key}")
            db.commit()
            return 0
        if args.cmd == "dkim-rotate":
            from .dkim_rotation import run as rotate

            res = rotate(db)
            switched = db.info.pop("dkim_switched", False)
            db.info.pop("cluster_push", None)
            db.commit()
            if switched:
                from .cluster.peer import push_all

                push_all()
            if not res.ran:
                print(f"not run: {res.reason}")
                return 0 if res.reason.startswith("not_leader") else 1
            for e in res.events:
                print(e)
            for e in res.errors:
                print(e, file=sys.stderr)
            return 1 if res.errors else 0
        if args.cmd in ("cluster-sync", "cluster-leave"):
            from .cluster import peer, state

            if not state.enabled(db):
                if args.cmd == "cluster-leave":
                    print("This server is not in a cluster", file=sys.stderr)
                return 0
            if args.cmd == "cluster-leave":
                peer.leave(db)
                print("Left the cluster; this server keeps its configuration")
                return 0
            # A peer that is down is shown on the Cluster page; it is not a failure of this timer.
            for name, result in peer.sync_all(db).items():
                print(f"{name}: {result}")
            return 0
        if args.cmd == "has-users":
            return 0 if db.execute(select(func.count(User.id))).scalar_one() else 1
        if args.cmd == "gateway-apply":
            from fastapi import HTTPException

            from .gateway import apply_changes, ensure_imported

            ensure_imported(db)
            try:
                result = apply_changes(db)
            except HTTPException as e:
                print(f"gateway apply failed: {e.detail}", file=sys.stderr)
                return 1
            db.commit()
            print(f"changed: {', '.join(result.get('changed') or []) or 'nothing'}")
            return 0
        if args.cmd == "write-lists":
            from .lists import write_maps

            counts = write_maps(db)
            print(f"allow: {counts['allow']}, block: {counts['block']}")
            return 0
        if args.cmd == "quarantine-report":
            from .digest import run

            res = run(db, force=args.now)
            if not res.ran:
                print(f"not sent: {res.reason}")
                return 0 if res.reason in ("disabled", "not_due") else 1
            print(f"sent {res.sent}, skipped {res.skipped} (nothing new), failed {len(res.failed)}")
            for f in res.failed:
                print(f"  {f}", file=sys.stderr)
            return 1 if res.failed else 0
        if args.cmd == "monthly-report":
            from .monthly import run as monthly_run
            from .usage import run as usage_run

            try:
                for m in usage_run(db):
                    print(f"stored the usage of {m}")
            except Exception as e:  # noqa: BLE001 - the monthly report must still go out
                db.rollback()
                print(f"usage measurement failed: {e}", file=sys.stderr)
            res = monthly_run(db, force=args.now)
            for m in res.stored:
                print(f"stored the numbers of {m}")
            if not res.ran:
                print(f"not sent: {res.reason}")
                return 0
            print(f"report {res.month}: sent {res.sent}, skipped {res.skipped} (switched off or no domains), failed {len(res.failed)}")
            for f in res.failed:
                print(f"  {f}", file=sys.stderr)
            return 1 if res.failed else 0
        email = args.email.strip().lower()
        user = db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
        if args.cmd == "reset-2fa":
            if not user:
                print(f"No user {email}", file=sys.stderr)
                return 1
            from . import twofactor
            from .audit import audit
            from .models import AuthSession

            twofactor.disable(user)
            db.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
            audit(db, None, None, "2fa_reset", user.email, via="cli")
            db.commit()
            print(f"Two-factor login turned off for {email}")
            return 0
        password = generate_password()
        if args.cmd == "create-admin":
            if user:
                print(f"User {email} already exists", file=sys.stderr)
                return 1
            db.add(User(email=email, name=args.name, role="admin", language=args.language, password_hash=hash_password(password)))
        else:
            if not user:
                print(f"No user {email}", file=sys.stderr)
                return 1
            user.password_hash = hash_password(password)
        db.commit()
        print(password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
