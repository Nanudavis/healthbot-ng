"""Hugging Face Inference API embeddings provider (cloud deployment).

The HF provider serves the same sentence-transformer model remotely, so
an index built with the local provider stays valid on a host without
torch. These tests cover provider selection, the HTTP contract, vector
normalisation, failure behaviour, and end-to-end retrieval against a
dumped index (network-free).
"""

import math

import pytest

from app import config, rag


@pytest.fixture(autouse=True)
def reset_store():
    rag.reset_store()
    yield
    rag.reset_store()


def test_hf_requires_token():
    with pytest.raises(RuntimeError, match="HF_API_TOKEN"):
        rag.HfInferenceEmbeddings(model="m", token="")


def test_embeddings_selects_hf_provider(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "hf")
    monkeypatch.setattr(config, "HF_API_TOKEN", "tok")
    emb = rag._embeddings()
    assert isinstance(emb, rag.HfInferenceEmbeddings)
    assert emb.model == config.HF_EMBEDDING_MODEL


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    last = None

    def __init__(self, payload, **kwargs):
        self.payload = payload
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, headers=None, json=None):
        FakeClient.last = {"url": url, "headers": headers, "json": json}
        return FakeResponse(self.payload)


def test_embed_query_calls_hf_endpoint_and_normalises(monkeypatch):
    monkeypatch.setattr(rag.httpx, "Client", lambda **kw: FakeClient([3.0, 4.0], **kw))
    emb = rag.HfInferenceEmbeddings(model="org/model", token="tok")
    vec = emb.embed_query("fever and headache")
    assert FakeClient.last["url"].endswith("/models/org/model")
    assert FakeClient.last["headers"]["Authorization"] == "Bearer tok"
    assert FakeClient.last["json"]["inputs"] == ["fever and headache"]
    assert FakeClient.last["json"]["wait_for_model"] is True
    # 3-4-5 triangle: normalised [0.6, 0.8]
    assert vec == pytest.approx([0.6, 0.8])


def test_embed_documents_handles_batch_shape(monkeypatch):
    payload = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    monkeypatch.setattr(rag.httpx, "Client", lambda **kw: FakeClient(payload, **kw))
    emb = rag.HfInferenceEmbeddings(model="org/model", token="tok")
    vecs = emb.embed_documents(["a", "b"])
    assert vecs[0] == pytest.approx([1.0, 0.0, 0.0])
    assert vecs[1] == pytest.approx([0.0, 1.0, 0.0])


def test_http_failure_raises_clean_runtime_error(monkeypatch):
    class BadClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def post(self, *a, **kw):
            raise rag.httpx.ConnectError("dial failed")

    monkeypatch.setattr(rag.httpx, "Client", BadClient)
    emb = rag.HfInferenceEmbeddings(model="org/model", token="tok")
    with pytest.raises(RuntimeError, match="HF embeddings unavailable"):
        emb.embed_query("x")


def test_zero_vector_is_returned_unchanged():
    assert rag.HfInferenceEmbeddings._normalise([0.0, 0.0]) == [0.0, 0.0]


def test_retrieval_with_hf_embeddings_against_dumped_index(monkeypatch, tmp_path):
    """InMemoryVectorStore.load accepts the HF client and similarity search
    ranks by content once embeddings are provided — no network needed."""
    from langchain_core.documents import Document
    from langchain_core.vectorstores import InMemoryVectorStore

    # deterministic content-based 384-dim mock: fever text -> left half,
    # hygiene text -> right half
    def fake_embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            low = t.lower()
            v = [
                1.0 if ("fever" in low and i < 192) or ("hands" in low and i >= 192) else 0.0
                for i in range(384)
            ]
            out.append(self._normalise(v))
        return out

    monkeypatch.setattr(rag.HfInferenceEmbeddings, "_embed", fake_embed)
    emb = rag.HfInferenceEmbeddings(model="m", token="tok")

    store = InMemoryVectorStore(emb)
    store.add_documents(
        [
            Document(page_content="Fever in children needs a malaria test." * 2,
                     metadata={"source": "protocol.pdf", "page": 0}),
            Document(page_content="Wash hands with soap and water." * 2,
                     metadata={"source": "protocol.pdf", "page": 1}),
        ]
    )
    idx = tmp_path / "idx.json"
    store.dump(str(idx))

    reloaded = InMemoryVectorStore.load(str(idx), emb)
    hits = reloaded.similarity_search("child fever", k=1)
    assert hits, "retrieval should return at least one document"
    assert "malaria" in hits[0].page_content
