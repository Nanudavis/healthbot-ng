"""Database-backed session store (SESSION_STORE=db)."""

from datetime import datetime, timedelta, timezone

import pytest

from app import config, db
from app.models import SessionMessage
from app.sessions import DbSessionStore, SessionStore, make_session_store


@pytest.fixture
def db_store(monkeypatch):
    monkeypatch.setattr(config, "SESSION_STORE", "db")
    return make_session_store(max_messages=4, ttl_seconds=60)


def test_db_store_roundtrip(db_store):
    sid = DbSessionStore.anonymise("+2348011111111")
    db_store.append(sid, "user", "hello")
    db_store.append(sid, "assistant", "hi")
    db_store.set_meta(sid, "language", "pidgin")
    assert db_store.history(sid) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert db_store.get_meta(sid, "language") == "pidgin"


def test_db_store_ttl_purges_stale_rows(db_store):
    sid = "stalesid"
    with db.get_session() as s:
        s.add(
            SessionMessage(
                session_id=sid,
                role="user",
                content="old",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(seconds=120),
            )
        )
        s.commit()
    assert db_store.history(sid) == []


def test_db_store_history_cap(db_store):
    sid = "capsid"
    for i in range(10):
        db_store.append(sid, "user", f"m{i}")
    hist = db_store.history(sid)
    assert len(hist) == 4
    assert hist[-1]["content"] == "m9"


def test_db_store_reset_clears(db_store):
    sid = "resetid"
    db_store.append(sid, "user", "x")
    db_store.set_meta(sid, "k", "v")
    db_store.reset(sid)
    assert db_store.history(sid) == []
    assert db_store.get_meta(sid, "k") is None


def test_factory_selects_backend(monkeypatch):
    monkeypatch.setattr(config, "SESSION_STORE", "memory")
    assert isinstance(make_session_store(), SessionStore)
    monkeypatch.setattr(config, "SESSION_STORE", "db")
    assert isinstance(make_session_store(), DbSessionStore)
