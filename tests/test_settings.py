"""Admin settings: switching provider/model and entering an API key."""

import pytest
from fastapi.testclient import TestClient

from app import config, conversation, db, rag, settings
from app.main import app

client = TestClient(app)
TOKEN = "admin-secret-token"
KEY = "sk-abcdefghijklmnopqrstuvwxyz1234"


@pytest.fixture
def admin_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/settings.db")
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o")
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


# ── Authorisation ───────────────────────────────────────────────

def test_writes_refused_when_no_admin_token_configured(admin_db, monkeypatch):
    """An unconfigured system must be locked, not open — the console has
    no user accounts."""
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    assert settings.writes_enabled() is False
    with pytest.raises(settings.NotAuthorised, match="ADMIN_TOKEN"):
        settings.update({"OPENAI_MODEL": "evil-model"}, "")
    assert config.OPENAI_MODEL == "gpt-4o"


def test_wrong_token_rejected(admin_db):
    with pytest.raises(settings.NotAuthorised, match="Invalid"):
        settings.update({"OPENAI_MODEL": "evil-model"}, "not-the-token")
    assert config.OPENAI_MODEL == "gpt-4o"


def test_correct_token_accepted(admin_db):
    settings.update({"OPENAI_MODEL": "llama-3.3-70b-versatile"}, TOKEN)
    assert config.OPENAI_MODEL == "llama-3.3-70b-versatile"


def test_only_whitelisted_settings_can_be_changed(admin_db):
    """A crafted request must not reach unrelated configuration."""
    with pytest.raises(ValueError, match="Not editable"):
        settings.update({"DATABASE_URL": "sqlite:///evil.db"}, TOKEN)
    with pytest.raises(ValueError, match="Not editable"):
        settings.update({"ADMIN_TOKEN": "hijacked"}, TOKEN)


# ── Secret handling ─────────────────────────────────────────────

def test_key_is_never_returned_in_full(admin_db):
    settings.update({"OPENAI_API_KEY": KEY}, TOKEN)
    body = settings.current()
    assert body["settings"]["OPENAI_API_KEY"]["value"] == f"…{KEY[-4:]}"
    assert KEY not in str(body)


def test_blank_key_keeps_the_existing_one(admin_db):
    """Editing the model must not silently wipe the API key."""
    settings.update({"OPENAI_API_KEY": KEY}, TOKEN)
    settings.update({"OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-4o-mini"}, TOKEN)
    assert config.OPENAI_API_KEY == KEY
    assert config.OPENAI_MODEL == "gpt-4o-mini"


def test_endpoint_never_leaks_the_key(admin_db):
    settings.update({"OPENAI_API_KEY": KEY}, TOKEN)
    assert KEY not in client.get("/api/settings").text


# ── Persistence and application ─────────────────────────────────

def test_settings_survive_restart(admin_db):
    settings.update({"OPENAI_MODEL": "gpt-5.6", "OPENAI_API_KEY": KEY}, TOKEN)
    # Simulate a restart: config reverts to .env defaults, then loads.
    config.OPENAI_MODEL = "gpt-4o"
    config.OPENAI_API_KEY = ""
    settings.load_into_config()
    assert config.OPENAI_MODEL == "gpt-5.6"
    assert config.OPENAI_API_KEY == KEY


def test_switching_provider_clears_learned_parameter_support(admin_db):
    """Models differ in which parameters they reject; carrying the old
    model's findings over would send wrong requests."""
    conversation._unsupported.add("response_format")
    settings.update({"OPENAI_MODEL": "claude-opus-4-8"}, TOKEN)
    assert conversation._unsupported == set()


def test_groq_preset_removed(admin_db):
    """Groq was dropped from the project; it must not be offered or
    accepted as a preset."""
    assert "groq" not in settings.PRESETS
    with pytest.raises(ValueError, match="Unknown preset"):
        settings.apply_preset("groq", TOKEN)


def test_deepseek_preset_points_at_v4_flash_and_local_embeddings(admin_db):
    """DeepSeek serves chat but no embedding API, so the preset must pair
    the OpenAI-compatible endpoint with on-device embeddings or RAG breaks."""
    settings.apply_preset("deepseek", TOKEN)
    assert config.OPENAI_BASE_URL == "https://api.deepseek.com/v1"
    assert config.OPENAI_MODEL == "deepseek-v4-flash"
    assert config.EMBEDDING_PROVIDER == "local"


def test_unknown_preset_rejected(admin_db):
    with pytest.raises(ValueError, match="Unknown preset"):
        settings.apply_preset("nonexistent", TOKEN)


def test_current_identifies_the_active_provider(admin_db):
    settings.apply_preset("agentrouter", TOKEN)
    assert settings.current()["provider"] == "agentrouter"
    settings.update({"OPENAI_BASE_URL": "https://something-else/v1"}, TOKEN)
    assert settings.current()["provider"] == "custom"


# ── Endpoints ───────────────────────────────────────────────────

def test_get_settings_endpoint(admin_db):
    body = client.get("/api/settings").json()
    assert body["writes_enabled"] is True
    assert "OPENAI_MODEL" in body["settings"]
    assert "groq" not in body["presets"]
    assert "deepseek" in body["presets"]


def test_post_settings_requires_token(admin_db):
    r = client.post("/api/settings", data={"admin_token": "wrong", "OPENAI_MODEL": "x"})
    assert r.status_code == 403


def test_post_settings_applies_change(admin_db):
    r = client.post(
        "/api/settings",
        data={"admin_token": TOKEN, "OPENAI_MODEL": "openai/gpt-oss-120b"},
    )
    assert r.status_code == 200
    assert config.OPENAI_MODEL == "openai/gpt-oss-120b"


def test_post_preset_endpoint(admin_db):
    r = client.post("/api/settings", data={"admin_token": TOKEN, "preset": "openai"})
    assert r.status_code == 200
    assert config.OPENAI_BASE_URL == ""
    assert config.OPENAI_MODEL == "gpt-4o"


def test_test_connection_endpoint_requires_token(admin_db):
    assert client.post("/api/settings/test", data={"admin_token": "no"}).status_code == 403


def test_test_connection_reports_failure_without_crashing(admin_db, monkeypatch):
    def _boom(messages):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    body = client.post("/api/settings/test", data={"admin_token": TOKEN}).json()
    assert body["ok"] is False
    assert "401" in body["error"]


def test_test_connection_success(admin_db, monkeypatch):
    monkeypatch.setattr(conversation, "_chat_completion", lambda m: '{"ok":true}')
    body = client.post("/api/settings/test", data={"admin_token": TOKEN}).json()
    assert body["ok"] is True


def test_test_connection_prompt_contains_the_word_json(admin_db, monkeypatch):
    """DeepSeek JSON mode rejects prompts that do not contain 'json' —
    the probe must include it or a working key is reported as broken."""
    captured = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return '{"ok":true}'

    class FakeEmbeddings:
        def embed_query(self, text):
            return [0.0]

    monkeypatch.setattr(conversation, "_chat_completion", fake_chat)
    monkeypatch.setattr(rag, "_embeddings", lambda: FakeEmbeddings())
    body = client.post("/api/settings/test", data={"admin_token": TOKEN}).json()
    assert body["ok"] is True
    assert "json" in captured["messages"][0]["content"].lower()
    assert body["embeddings"]["ok"] is True


def test_update_invalidates_rag_store_when_embeddings_change(admin_db, monkeypatch):
    """A cached vector store holds the OLD embedding client; switching
    embedding config must drop it or the change silently never applies."""
    calls = []
    monkeypatch.setattr(rag, "reset_store", lambda: calls.append(True))

    settings.update({"EMBEDDING_PROVIDER": "local"}, TOKEN)
    assert calls == [True]

    calls.clear()
    settings.update({"OPENAI_MODEL": "gpt-4o-mini"}, TOKEN)
    assert calls == []


def test_clearing_base_url_survives_restart(admin_db):
    """Blank on a non-secret setting means 'cleared to default' and must
    persist across a restart instead of resurrecting the .env value."""
    settings.update({"OPENAI_BASE_URL": "https://gateway.example/v1"}, TOKEN)
    settings.update({"OPENAI_BASE_URL": ""}, TOKEN)
    # Simulate restart: config reverts to a .env default, then loads.
    config.OPENAI_BASE_URL = "https://default.example/v1"
    settings.load_into_config()
    assert config.OPENAI_BASE_URL == ""


def test_env_example_is_a_valid_env_file():
    """It is the file everyone copies to .env — a malformed line there
    becomes a broken setup for whoever follows the README."""
    from dotenv import dotenv_values

    values = dotenv_values(".env.example")
    assert len(values) > 15
    # Values that must parse cleanly rather than absorbing a stray
    # comment fragment from an edit.
    assert values["LLM_TIMEOUT_SECONDS"] == "12"
    assert values["LLM_MAX_RETRIES"] == "3"
    assert values["LLM_MAX_BACKOFF_SECONDS"] == "20"
    for key, value in values.items():
        assert "\n" not in (value or ""), key
        assert not (value or "").startswith("#"), key


def test_env_example_ships_no_secrets():
    from dotenv import dotenv_values

    for key, value in dotenv_values(".env.example").items():
        if "KEY" in key or "TOKEN" in key:
            assert not value, f"{key} must ship blank"
