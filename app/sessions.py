"""Per-user conversation memory.

Phone numbers are never stored directly in this path. Sessions are keyed by a
stable SHA-256 hash, which is pseudonymous because it remains linkable.

Two backends behind the same interface:
- `SessionStore` — process-local dict (default; single worker).
- `DbSessionStore` — SQLAlchemy-backed (SESSION_STORE=db) so sessions
  survive restarts and work across workers. Opt-in because it persists
  transient conversation text; rows are TTL-purged.

Sessions expire after a period of inactivity. This is a clinical
requirement, not housekeeping: without it, someone who messaged about a
child's fever last week and messages again today would have the new
complaint triaged against stale context, and the engine could answer a
question the person never asked.
"""

import hashlib
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app import config, db
from app.models import SessionMessage, SessionMeta

# A triage conversation is a handful of turns over a few minutes. An
# hour is generous for someone on an intermittent connection, and short
# enough that a later message is treated as a new complaint.
DEFAULT_TTL_SECONDS = 3600


class SessionStore:
    def __init__(self, max_messages: int = 20, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._sessions: dict[str, list[dict]] = {}
        self._meta: dict[str, dict] = {}
        self._touched: dict[str, float] = {}
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def anonymise(phone_number: str) -> str:
        return hashlib.sha256(phone_number.encode()).hexdigest()[:16]

    def history(self, session_id: str) -> list[dict]:
        self._drop_if_stale(session_id)
        return self._sessions.get(session_id, [])

    def append(self, session_id: str, role: str, content: str) -> None:
        self._drop_if_stale(session_id)
        history = self._sessions.setdefault(session_id, [])
        history.append({"role": role, "content": content})
        if len(history) > self.max_messages:
            del history[: len(history) - self.max_messages]
        self._touched[session_id] = time.monotonic()

    def set_meta(self, session_id: str, key: str, value) -> None:
        self._drop_if_stale(session_id)
        self._meta.setdefault(session_id, {})[key] = value
        self._touched[session_id] = time.monotonic()

    def get_meta(self, session_id: str, key: str, default=None):
        self._drop_if_stale(session_id)
        return self._meta.get(session_id, {}).get(key, default)

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._meta.pop(session_id, None)
        self._touched.pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()
        self._meta.clear()
        self._touched.clear()

    def active_count(self) -> int:
        self.purge_expired()
        return len(self._sessions)

    def purge_expired(self) -> int:
        """Drop every session idle beyond the TTL. Returns how many."""
        cutoff = time.monotonic() - self.ttl_seconds
        stale = [sid for sid, seen in self._touched.items() if seen < cutoff]
        for sid in stale:
            self.reset(sid)
        return len(stale)

    def _drop_if_stale(self, session_id: str) -> None:
        seen = self._touched.get(session_id)
        if seen is not None and seen < time.monotonic() - self.ttl_seconds:
            self.reset(session_id)


class DbSessionStore:
    """Same interface as SessionStore, backed by the relational database.

    Uses the pseudonymised session hash as the key and purges rows older
    than the TTL on every access, so a stale complaint can never be
    triaged as though it were current.
    """

    def __init__(self, max_messages: int = 20, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def anonymise(phone_number: str) -> str:
        return hashlib.sha256(phone_number.encode()).hexdigest()[:16]

    def _now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _purge(self, session_id: str | None = None) -> None:
        cutoff = self._now() - timedelta(seconds=self.ttl_seconds)
        with db.get_session() as session:
            msgs = delete(SessionMessage).where(SessionMessage.created_at < cutoff)
            metas = delete(SessionMeta).where(SessionMeta.updated_at < cutoff)
            if session_id:
                msgs = msgs.where(SessionMessage.session_id == session_id)
                metas = metas.where(SessionMeta.session_id == session_id)
            session.execute(msgs)
            session.execute(metas)
            session.commit()

    def history(self, session_id: str) -> list[dict]:
        self._purge(session_id)
        with db.get_session() as session:
            rows = session.scalars(
                select(SessionMessage)
                .where(SessionMessage.session_id == session_id)
                .order_by(SessionMessage.id)
            ).all()
        return [{"role": r.role, "content": r.content} for r in rows]

    def append(self, session_id: str, role: str, content: str) -> None:
        self._purge(session_id)
        with db.get_session() as session:
            session.add(
                SessionMessage(
                    session_id=session_id,
                    role=role,
                    content=content,
                    created_at=self._now(),
                )
            )
            session.commit()
        # Enforce the history cap.
        with db.get_session() as session:
            rows = session.scalars(
                select(SessionMessage.id)
                .where(SessionMessage.session_id == session_id)
                .order_by(SessionMessage.id)
            ).all()
            excess = rows[: max(0, len(rows) - self.max_messages)]
            if excess:
                session.execute(
                    delete(SessionMessage).where(SessionMessage.id.in_(excess))
                )
                session.commit()

    def set_meta(self, session_id: str, key: str, value) -> None:
        self._purge(session_id)
        with db.get_session() as session:
            row = session.scalar(
                select(SessionMeta).where(
                    SessionMeta.session_id == session_id,
                    SessionMeta.key == key,
                )
            )
            if row:
                row.value = str(value)
                row.updated_at = self._now()
            else:
                session.add(
                    SessionMeta(
                        session_id=session_id,
                        key=key,
                        value=str(value),
                        updated_at=self._now(),
                    )
                )
            session.commit()

    def get_meta(self, session_id: str, key: str, default=None):
        self._purge(session_id)
        with db.get_session() as session:
            row = session.scalar(
                select(SessionMeta).where(
                    SessionMeta.session_id == session_id,
                    SessionMeta.key == key,
                )
            )
            return row.value if row else default

    def reset(self, session_id: str) -> None:
        with db.get_session() as session:
            session.execute(
                delete(SessionMessage).where(SessionMessage.session_id == session_id)
            )
            session.execute(delete(SessionMeta).where(SessionMeta.session_id == session_id))
            session.commit()

    def clear(self) -> None:
        with db.get_session() as session:
            session.execute(delete(SessionMessage))
            session.execute(delete(SessionMeta))
            session.commit()

    def active_count(self) -> int:
        cutoff = self._now() - timedelta(seconds=self.ttl_seconds)
        with db.get_session() as session:
            return len(
                session.scalars(
                    select(SessionMessage.session_id)
                    .where(SessionMessage.created_at >= cutoff)
                    .distinct()
                ).all()
            )

    def purge_expired(self) -> int:
        cutoff = self._now() - timedelta(seconds=self.ttl_seconds)
        with db.get_session() as session:
            stale = session.scalars(
                select(SessionMessage.session_id)
                .where(SessionMessage.created_at < cutoff)
                .distinct()
            ).all()
            for sid in stale:
                self.reset(sid)
            return len(stale)


def make_session_store(max_messages: int = 20, ttl_seconds: int = DEFAULT_TTL_SECONDS):
    """Factory: memory store by default, DB store when SESSION_STORE=db."""
    if config.SESSION_STORE == "db":
        return DbSessionStore(max_messages, ttl_seconds)
    return SessionStore(max_messages, ttl_seconds)
