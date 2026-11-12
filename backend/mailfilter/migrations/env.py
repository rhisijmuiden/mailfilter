from alembic import context

from mailfilter import models  # noqa: F401  (registers every table)
from mailfilter.db import Base, get_engine

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Tables created with raw SQL (mail trace) are managed by hand-written migrations.
    if type_ == "table" and (obj.info.get("skip_autogenerate") or name == "tracer_state"):
        return False
    if type_ == "index" and name and name.startswith(("trace_message", "trace_recipient")):
        return False
    return True


def run() -> None:
    with get_engine().connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


run()
