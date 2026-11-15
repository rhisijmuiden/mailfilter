"""Database engine and session handling (SQLAlchemy 2)."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    # A database created on a server without a UTF-8 locale has encoding SQL_ASCII; psycopg then
    # returns bytes and SQLAlchemy fails on the first connect. Asking for UTF-8 works on both.
    args = {"client_encoding": "utf8"} if url.startswith("postgresql") else {}
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=args)


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the handler."""
    db = _sessionmaker()()
    db.info["web"] = True  # a person's change (see cluster/tracking.py for backup servers)
    try:
        yield db
    finally:
        db.close()


def session() -> Session:
    return _sessionmaker()()
