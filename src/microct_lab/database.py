"""SQLAlchemy engine, session, and Base. The DB is the 'registry' and lives in STATE_DIR."""
from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

settings.ensure_dirs()

_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")     # readers don't block the writer
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added to pre-existing tables after v0.1. create_all() only creates NEW
# tables; it never alters an existing one, so we add missing columns by hand. Each
# entry is (column, "SQL type + default") — safe/idempotent, checked before adding.
_ADDED_COLUMNS = {
    "models": [
        ("archived", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "datasets": [
        ("type", "VARCHAR(32) NOT NULL DEFAULT 'uct'"),
        ("organism", "VARCHAR(64)"),
        ("set_id", "INTEGER"),
        ("experiment_id", "INTEGER"),
        ("nas_relpath", "VARCHAR(1024)"),
        ("archived", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "runs": [
        ("archived", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
}


def _add_missing_columns() -> None:
    """Idempotently add columns introduced after the DB was first created."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue  # fresh table — create_all already made it complete
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine)
    _add_missing_columns()
