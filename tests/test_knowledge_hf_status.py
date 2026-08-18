"""The 'hf' embedding provider must report the right model everywhere."""

from app import config, knowledge, rag


def test_index_status_hf_model_and_match(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "hf")
    monkeypatch.setattr(config, "HF_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "text-embedding-3-small")

    # index meta written by the hf ingest path
    meta = {
        "embedding_provider": "hf",
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "chunks": 10,
    }
    monkeypatch.setattr(rag, "index_meta", lambda: meta)

    status = knowledge.index_status()
    assert status["embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert status["index_embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert status["index_matches_config"] is True


def test_index_status_hf_mismatch_is_flagged(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "hf")
    monkeypatch.setattr(config, "HF_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(
        rag,
        "index_meta",
        lambda: {
            "embedding_provider": "local",
            "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "chunks": 10,
        },
    )
    status = knowledge.index_status()
    assert status["index_matches_config"] is False
