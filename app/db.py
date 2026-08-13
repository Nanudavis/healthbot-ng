"""Data layer (Layer 4): SQLAlchemy over SQLite in development,
PostgreSQL in deployment — switched purely by DATABASE_URL."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app import config


class Base(DeclarativeBase):
    pass


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = config.DATABASE_URL
        kwargs = {}
        if url.startswith("sqlite:///"):
            db_path = url.removeprefix("sqlite:///")
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine())
    from app import migrations

    migrations.migrate()


def get_session() -> Session:
    return Session(get_engine())


def reset_engine() -> None:
    """Dispose the cached engine (tests switch DATABASE_URL)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
