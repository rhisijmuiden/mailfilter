from alembic import context

from mailfilter import models  # noqa: F401  (registers every table)
from mailfilter.db import Base, get_engine

target_metadata = Base.metadata


def run() -> None:
    with get_engine().connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
