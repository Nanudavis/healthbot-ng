"""Cloud-deployment settings: the 'deepseek-hf' preset and HF-editable keys.

The deployed (Railway) configuration is DeepSeek chat + Hugging Face
hosted embeddings. Before this suite, the console could not represent
that configuration: applying the DeepSeek preset on the server would
flip embeddings to the on-device (torch) provider and break retrieval on
a small instance. These tests pin the preset, the editable keys, the
store invalidation, and the connection-test labelling.
"""

import pytest

from app import config, db, rag, settings

TOKEN = "console-token"


@pytest.fixture(autouse=True)
def admin_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/settings.db")
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


def test_hf_preset_sets_hf_embeddings_and_deepseek_chat():
    result = settings.apply_preset("deepseek-hf", TOKEN)
    assert config.OPENAI_BASE_URL == "https://api.deepseek.com/v1"
    assert config.OPENAI_MODEL == "deepseek-v4-flash"
    assert config.EMBEDDING_PROVIDER == "hf"
    assert "EMBEDDING_PROVIDER" in result["applied"]


def test_hf_preset_appears_in_presets_listing():
    presets = settings.current()["presets"]
    assert "deepseek-hf" in presets
    assert presets["deepseek-hf"]["EMBEDDING_PROVIDER"] == "hf"


def test_hf_keys_are_editable_and_masked():
    settings.update(
        {"HF_API_TOKEN": "hf_secret_token", "HF_EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5"},
        TOKEN,
    )
    current = settings.current()["settings"]
    assert current["HF_API_TOKEN"]["is_set"] is True
    assert "hf_secret_token" not in current["HF_API_TOKEN"]["value"]
    assert current["HF_API_TOKEN"]["value"].endswith("…ken") or current["HF_API_TOKEN"]["value"].endswith("ken")
    assert current["HF_EMBEDDING_MODEL"]["value"] == "BAAI/bge-small-en-v1.5"


def test_hf_setting_change_invalidates_rag_store(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "reset_store", lambda: calls.append(True))
    settings.update({"HF_API_TOKEN": "new-token"}, TOKEN)
    assert calls == [True]

    calls.clear()
    settings.update({"HF_EMBEDDING_MODEL": "another/model"}, TOKEN)
    assert calls == [True]

    calls.clear()
    settings.update({"OPENAI_MODEL": "deepseek-v4-pro"}, TOKEN)
    assert calls == []


def test_unknown_preset_still_rejected():
    with pytest.raises(ValueError, match="Unknown preset"):
        settings.apply_preset("not-a-preset", TOKEN)


def test_connection_test_labels_hf_embeddings(monkeypatch):
    import app.conversation as conversation

    class FakeEmbeddings:
        def embed_query(self, text):
            return [0.0]

    monkeypatch.setattr(conversation, "_chat_completion", lambda m: '{"ok":true}')
    monkeypatch.setattr(rag, "_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "hf")
    monkeypatch.setattr(config, "HF_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

    body = settings.test_connection()
    assert body["ok"] is True
    assert body["embeddings"]["ok"] is True
    assert body["embeddings"]["provider"] == "hf"
    assert body["embeddings"]["model"] == "BAAI/bge-small-en-v1.5"


def test_connection_test_hf_failure_reports_hf(monkeypatch):
    import app.conversation as conversation

    def boom():
        raise RuntimeError("HF_API_TOKEN is not set")

    class BadEmbeddings:
        def embed_query(self, text):
            raise RuntimeError("HF_API_TOKEN is not set")

    monkeypatch.setattr(conversation, "_chat_completion", lambda m: '{"ok":true}')
    monkeypatch.setattr(rag, "_embeddings", lambda: BadEmbeddings())
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "hf")

    body = settings.test_connection()
    assert body["embeddings"]["ok"] is False
    assert body["embeddings"]["provider"] == "hf"
    assert "HF_API_TOKEN" in body["embeddings"]["error"]
