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
        ("subtype", "VARCHAR(32)"),
        ("digit_id", "VARCHAR(8)"),
        ("unamputated", "BOOLEAN NOT NULL DEFAULT 0"),
        ("project_id", "INTEGER"),
        ("edited_fields", "JSON NOT NULL DEFAULT '[]'"),
        ("crop_box", "JSON"),
    ],
    "runs": [
        ("archived", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "projects": [
        ("project_lead", "VARCHAR(200)"),
    ],
    "dataset_sets": [
        ("organism", "VARCHAR(64)"),
        ("subtype", "VARCHAR(32)"),
    ],
    "analyses": [
        ("data", "JSON"),
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


def _backfill() -> None:
    """One-time-ish (idempotent, cheap) data fixups after column adds.

    * subtype: a dataset already tagged with an anatomy tag (the old gate,
      default 'phalanx') is a digit dataset — stamp it so the new subtype gate
      doesn't lock out data that was working yesterday.
    * project_id: derived from the experiment for rows that predate the column,
      so project dataset counts and direct-membership queries see them.
    * digit_id: parsed from the name where the pattern is unambiguous.
    Only NULL fields are ever written, so user edits are never overwritten.
    """
    from .config import settings
    from .models import Dataset, Experiment
    from .registry import parse_digit_id

    anatomy = set(settings.morph_anatomy_tag_list)
    with SessionLocal() as db:
        exp_project = dict(db.query(Experiment.id, Experiment.project_id).all())
        changed = False
        for d in db.query(Dataset).all():
            if d.subtype is None and anatomy & {str(t).strip().lower() for t in (d.tags or [])}:
                d.subtype = "digit"
                changed = True
            if d.project_id is None and d.experiment_id in exp_project:
                d.project_id = exp_project[d.experiment_id]
                changed = True
            if d.digit_id is None and "digit_id" not in (d.edited_fields or []):
                parsed = parse_digit_id(d.name)
                if parsed:
                    d.digit_id = parsed
                    changed = True
        if changed:
            db.commit()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _backfill()
