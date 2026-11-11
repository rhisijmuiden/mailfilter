"""mailfilter-admin: command line tasks for the installer and for emergencies.

  mailfilter-admin migrate                     bring the database schema up to date
  mailfilter-admin create-admin EMAIL          create an admin (prints a generated password)
  mailfilter-admin set-password EMAIL          set a new generated password
  mailfilter-admin has-users                   exit 0 when at least one user exists
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
    from .db import session
    from .models import User
    from .security import generate_password, hash_password

    p = argparse.ArgumentParser(prog="mailfilter-admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    c = sub.add_parser("create-admin")
    c.add_argument("email")
    c.add_argument("--name", default="Admin")
    c.add_argument("--language", choices=["en", "nl"], default="en")
    s = sub.add_parser("set-password")
    s.add_argument("email")
    sub.add_parser("has-users")
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        migrate()
        return 0
    with session() as db:
        if args.cmd == "has-users":
            return 0 if db.execute(select(func.count(User.id))).scalar_one() else 1
        email = args.email.strip().lower()
        user = db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none()
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
