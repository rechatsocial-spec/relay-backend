"""
Optional: run the API against SQLite instead of Postgres, for a quick local
check without installing/starting Postgres. Not for anything beyond that —
Postgres-specific column types (UUID, ARRAY) are monkey-patched to SQLite
equivalents here, which is fine for trying things out but isn't how this
runs in the README's normal Postgres flow.

    python run_dev_sqlite.py              # port 8000 (default)
    python run_dev_sqlite.py --port 8001  # any other port
    python run_dev_sqlite.py --reset      # delete dev.db and reseed

Then in another terminal:
    node integration_test.mjs      # exercises every real endpoint end to end
or open http://localhost:8000/docs and try it by hand.
"""
import argparse
import os
import uuid

from sqlalchemy import CHAR, JSON, TypeDecorator
import sqlalchemy.dialects.postgresql as pg


class _SqliteUUID(TypeDecorator):
    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return str(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return uuid.UUID(value) if value is not None else None


class _SqliteARRAY(TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self, item_type=None, *a, **k):
        super().__init__()

    def process_bind_param(self, value, dialect):
        return value

    def process_result_value(self, value, dialect):
        return value or []


def main(default_port: int = 8000):
    parser = argparse.ArgumentParser(description="Run Relay CRM API on SQLite")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", default_port)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reset", action="store_true", help="delete dev.db and reseed")
    args = parser.parse_args()

    pg.UUID = lambda as_uuid=False, *a, **k: _SqliteUUID()
    pg.ARRAY = lambda item_type=None, *a, **k: _SqliteARRAY(item_type)

    # SQLite mode always uses its own file, even if .env points at Postgres.
    os.environ["DATABASE_URL"] = "sqlite:///./dev.db"
    os.environ.setdefault("SECRET_KEY", "dev-secret-not-for-production")
    if args.reset and os.path.exists("dev.db"):
        os.remove("dev.db")

    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.create_all(bind=engine)
    from app.db.auto_migrate import upgrade_schema
    upgrade_schema(engine)

    import app.seed as seed_mod
    seed_mod.run()

    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
