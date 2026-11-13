"""A server without a UTF-8 locale (minimal Debian container, Proxmox LXC) gives PostgreSQL databases
the encoding SQL_ASCII. psycopg then returns bytes and SQLAlchemy failed on the first connect, so the
installer stopped at "mailfilter-admin migrate". The engine asks for UTF-8 so this works."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

import mailfilter.db as dbmod


@pytest.fixture
def sql_ascii_url():
    url = dbmod.get_engine().url
    name = (url.database or "mailfilter_test") + "_sql_ascii"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f"CREATE DATABASE \"{name}\" ENCODING 'SQL_ASCII' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"))
    yield url.set(database=name)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def test_engine_works_on_sql_ascii_database(sql_ascii_url, monkeypatch):
    from alembic import command

    from mailfilter.cli import _alembic_config

    settings = SimpleNamespace(database_url=sql_ascii_url.render_as_string(hide_password=False))
    monkeypatch.setattr(dbmod, "get_settings", lambda: settings)
    engine = dbmod.get_engine.__wrapped__()
    try:
        with engine.connect() as c:
            assert c.execute(text("SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()")).scalar() == "SQL_ASCII"
            assert c.execute(text("SELECT 'café'::text")).scalar() == "café"
        monkeypatch.setattr(dbmod, "get_engine", lambda: engine)  # migrations/env.py takes the engine from here
        command.upgrade(_alembic_config(), "head")
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1
    finally:
        engine.dispose()
