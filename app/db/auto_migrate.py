"""
Tiny dev-friendly schema upgrader, run on every startup after create_all().

create_all() only creates *missing tables* — it never adds a new column to a
table that already exists, so adding a field to a model would otherwise
require wiping the database. This adds:
  - columns that exist on a model but not in the DB (added as NULLable,
    with the model's server_default if it has one)
  - new values on Postgres enum types (e.g. a new MessageStatus)

It never drops, renames, or changes anything. For production with real data,
move to Alembic migrations (see README) and delete the call in main.py.
"""
import logging

from sqlalchemy import Enum as SAEnum, inspect, text

from app.db.base import Base

log = logging.getLogger("relay.migrate")


def _add_missing_columns(engine) -> list[str]:
    added = []
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                default = ""
                if col.server_default is not None:
                    arg = col.server_default.arg
                    default_sql = arg if isinstance(arg, str) else str(arg.compile(dialect=engine.dialect))
                    default = f" DEFAULT {default_sql}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{default}'))
                added.append(f"{table.name}.{col.name}")
    return added


def _add_missing_enum_values(engine) -> list[str]:
    if engine.dialect.name != "postgresql":
        return []  # SQLite stores enums as plain strings
    added = []
    enum_types = {}
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, SAEnum) and col.type.name:
                enum_types[col.type.name] = col.type.enums
    # ALTER TYPE ... ADD VALUE must not share a transaction with its first use.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for type_name, labels in enum_types.items():
            rows = conn.execute(
                text("SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid WHERE t.typname = :n"),
                {"n": type_name},
            ).scalars().all()
            if not rows:
                continue  # type not created yet — create_all will make it complete
            for label in labels:
                if label not in rows:
                    conn.execute(text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{label}'"))
                    added.append(f"{type_name}.{label}")
    return added


def upgrade_schema(engine) -> None:
    changes = _add_missing_columns(engine) + _add_missing_enum_values(engine)
    if changes:
        log.warning("Auto-migrated schema: %s", ", ".join(changes))
        print(f"[auto-migrate] added: {', '.join(changes)}")
