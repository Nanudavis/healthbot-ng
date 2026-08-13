"""Minimal, ordered schema migrations.

`create_all` only creates missing tables — it cannot add indexes to (or
alter) existing ones. This module applies versioned, idempotent
migrations at startup after create_all and records what ran, so an
existing database is upgraded in place without manual DDL. Fail-open: a
migration failure is logged and never blocks a patient reply.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import Index, text

log = logging.getLogger(__name__)


def _engine():
    from app import db

    return db.get_engine()


def _applied() -> set[str]:
    with _engine().begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR(64) UNIQUE NOT NULL, "
                "applied_at DATETIME NOT NULL)"
            )
        )
        rows = conn.execute(text("SELECT name FROM schema_migrations")).all()
    return {r[0] for r in rows}


def _migrate_001_triage_indexes() -> None:
    """Backfill the triage indexes on databases created before the model
    declared them. Fresh databases get them from create_all; here we
    check existence explicitly because SQLite ignores checkfirst=True."""
    from app.models import TriageRecord
    from sqlalchemy import inspect

    inspector = inspect(_engine())
    existing = {
        i["name"] for i in inspector.get_indexes("triage_records")
    }
    for name, columns in (
        ("ix_triage_records_session_id", [TriageRecord.session_id]),
        ("ix_triage_records_created_at_level", [TriageRecord.created_at, TriageRecord.level]),
    ):
        if name not in existing:
            Index(name, *columns).create(bind=_engine())


MIGRATIONS = [
    ("001_triage_indexes", _migrate_001_triage_indexes),
]


def migrate() -> None:
    try:
        applied = _applied()
        for name, fn in MIGRATIONS:
            if name in applied:
                continue
            fn()  # idempotent; safe to re-run if the record insert below fails
            with _engine().begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO schema_migrations (name, applied_at) "
                        "VALUES (:name, :applied_at)"
                    ),
                    {
                        "name": name,
                        "applied_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    },
                )
            log.info("Applied schema migration %s", name)
    except Exception:
        log.exception("Schema migration failed; continuing with existing schema")


def applied_migrations() -> list[str]:
    return sorted(_applied())
