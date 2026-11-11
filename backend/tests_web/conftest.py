"""Tests run against a real PostgreSQL database (CI starts one as a service).

Set MAILFILTER_TEST_DB_URL, e.g.
postgresql+psycopg://mailfilter:dev@127.0.0.1:5432/mailfilter_test
"""

from __future__ import annotations

import os

import pytest

os.environ["MAILFILTER_DB_URL"] = os.environ.get(
    "MAILFILTER_TEST_DB_URL", "postgresql+psycopg://mailfilter:dev@127.0.0.1:5433/mailfilter_test"
)
os.environ["MF_ETC"] = "/nonexistent"
os.environ["MF_COOKIE_SECURE"] = "0"
os.environ["APP_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mailfilter.cli import migrate  # noqa: E402
from mailfilter.db import get_engine, session  # noqa: E402
from mailfilter.main import app  # noqa: E402
from mailfilter.models import Domain, User  # noqa: E402
from mailfilter.security import hash_password, login_throttle  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture(scope="session", autouse=True)
def schema():
    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate()
    yield


@pytest.fixture(autouse=True)
def clean():
    yield
    with get_engine().begin() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        c.execute(text("TRUNCATE " + ",".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    login_throttle._fails.clear()


@pytest.fixture
def users():
    with session() as db:
        d1, d2 = Domain(name="rhis.nl"), Domain(name="klant.nl")
        admin = User(email="admin@rhis.nl", name="Reinder", role="admin", password_hash=hash_password(PASSWORD))
        cust = User(email="info@klant.nl", name="Klant", role="customer", language="nl", password_hash=hash_password(PASSWORD))
        cust.domains = [d2]
        db.add_all([d1, d2, admin, cust])
        db.commit()
    return {"admin": "admin@rhis.nl", "customer": "info@klant.nl"}


class Client(TestClient):
    def __init__(self):
        super().__init__(app, headers={"X-Requested-With": "mailfilter"})

    def login(self, email: str, password: str = PASSWORD):
        r = self.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return self


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin(users):
    return Client().login(users["admin"])


@pytest.fixture
def customer(users):
    return Client().login(users["customer"])
