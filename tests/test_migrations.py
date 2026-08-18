"""Schema migrations: versioned, idempotent, and the missing indexes."""

from sqlalchemy import text

from app import db, migrations


def _index_names() -> set[str]:
    with db.get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'index'")
        ).all()
    return {r[0] for r in rows}


def test_migrations_apply_and_record():
    migrations.migrate()
    assert "001_triage_indexes" in migrations.applied_migrations()
    indexes = _index_names()
    assert "ix_triage_records_session_id" in indexes
    assert "ix_triage_records_created_at_level" in indexes


def test_migrations_are_idempotent():
    migrations.migrate()
    first = migrations.applied_migrations()
    migrations.migrate()
    assert migrations.applied_migrations() == first
    # checkfirst prevents duplicate index errors on re-run
    assert "ix_triage_records_session_id" in _index_names()


def test_migrations_endpoint():
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/api/observability/migrations")
    assert r.status_code == 200
    assert "001_triage_indexes" in r.json()["applied"]
