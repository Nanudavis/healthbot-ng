import pytest

from app import config, conversation, db, security


@pytest.fixture(autouse=True)
def fresh_sessions():
    conversation.store.clear()
    yield
    conversation.store.clear()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own SQLite file so nothing touches data/healthbot.db."""
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/unit.db")
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


@pytest.fixture(autouse=True)
def no_twilio_secrets(monkeypatch):
    """Tests must never depend on the developer's .env credentials.
    Signature verification is exercised by the dedicated security tests,
    which set the token themselves; everything else posts unsigned."""
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "CONSOLE_AUTH_REQUIRED", False)
    monkeypatch.setattr(config, "SESSION_STORE", "memory")
    monkeypatch.setattr(config, "WHATSAPP_ASYNC_OUTBOUND", False)
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "")
    monkeypatch.setattr(config, "TWILIO_WHATSAPP_NUMBER", "")
    security.reset_message_dedupe()
    security.reset_rate_limits()
    yield


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the OpenAI call with a canned reply; records every prompt."""
    calls: list[list[dict]] = []

    def _fake(messages):
        calls.append(messages)
        return (
            '{"triage": "PENDING", "reason": "Need the child\'s age", '
            '"reply": "How old is the child?"}'
        )

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    return calls
