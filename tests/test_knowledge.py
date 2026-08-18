"""Knowledge-base management: the protocol documents behind RAG.

The corpus is the system's clinical authority — what it retrieves is
what triage guidance is grounded in — so adding to it is gated and
validated like any other change to clinical behaviour.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, knowledge, settings
from app.main import app

client = TestClient(app)
TOKEN = "admin-secret-token"
PDF_BYTES = b"%PDF-1.4\n% minimal\n"


@pytest.fixture
def kb(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROTOCOLS_DIR", str(tmp_path / "protocols"))
    monkeypatch.setattr(config, "LOCAL_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(config, "PINECONE_API_KEY", "")
    yield tmp_path / "protocols"


# ── Filename safety ─────────────────────────────────────────────

@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("who_imci.pdf", "who_imci.pdf"),
        ("WHO IMCI booklet.pdf", "WHO_IMCI_booklet.pdf"),
        ("notes.TXT", "notes.TXT"),
    ],
)
def test_safe_name_accepts_documents(given, expected):
    assert knowledge.safe_name(given) == expected


@pytest.mark.parametrize(
    "given",
    ["../../etc/passwd.pdf", "/etc/shadow.pdf", "..%2f..%2fx.pdf", "sub/dir/file.pdf"],
)
def test_safe_name_blocks_path_traversal(kb, given):
    """A compromised token must not become arbitrary file write.

    The property that matters is that the resolved target stays inside
    the protocols directory — not that the name looks tidy. A flat name
    containing dots (e.g. from a URL-encoded attempt) is harmless.
    """
    name = knowledge.safe_name(given)
    assert "/" not in name and "\\" not in name
    target = (knowledge.protocols_dir() / name).resolve()
    assert target.parent == knowledge.protocols_dir().resolve()


@pytest.mark.parametrize("given", ["script.py", "shell.sh", "payload.exe", "noextension"])
def test_safe_name_rejects_non_documents(given):
    with pytest.raises(ValueError, match="files are accepted"):
        knowledge.safe_name(given)


def test_safe_name_rejects_empty():
    with pytest.raises(ValueError):
        knowledge.safe_name("")


# ── Upload validation ───────────────────────────────────────────

def test_upload_saves_document(kb):
    result = knowledge.save_upload("guideline.pdf", PDF_BYTES)
    assert result["name"] == "guideline.pdf"
    assert result["replaced"] is False
    assert (kb / "guideline.pdf").exists()


def test_upload_reports_replacement(kb):
    knowledge.save_upload("guideline.pdf", PDF_BYTES)
    assert knowledge.save_upload("guideline.pdf", PDF_BYTES)["replaced"] is True


def test_upload_rejects_empty_file(kb):
    with pytest.raises(ValueError, match="empty"):
        knowledge.save_upload("guideline.pdf", b"")


def test_upload_rejects_oversized_file(kb, monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(ValueError, match="limit"):
        knowledge.save_upload("big.pdf", PDF_BYTES + b"x" * 200)


def test_upload_rejects_mislabelled_pdf(kb):
    """A file named .pdf that is not a PDF would contribute nothing to
    the corpus and fail silently at ingest."""
    with pytest.raises(ValueError, match="not a PDF"):
        knowledge.save_upload("fake.pdf", b"this is plain text, not a pdf")


def test_text_documents_are_not_pdf_checked(kb):
    knowledge.save_upload("notes.txt", b"Fever over 24 hours needs same-day review.")
    assert (kb / "notes.txt").exists()


# ── Listing and deletion ────────────────────────────────────────

def test_list_documents(kb):
    knowledge.save_upload("a.pdf", PDF_BYTES)
    knowledge.save_upload("b.txt", b"text")
    names = {d["name"] for d in knowledge.list_documents()}
    assert names == {"a.pdf", "b.txt"}


def test_list_ignores_unrelated_files(kb):
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "stray.bin").write_bytes(b"junk")
    knowledge.save_upload("real.pdf", PDF_BYTES)
    assert [d["name"] for d in knowledge.list_documents()] == ["real.pdf"]


def test_delete_document(kb):
    knowledge.save_upload("a.pdf", PDF_BYTES)
    assert knowledge.delete_document("a.pdf") is True
    assert knowledge.list_documents() == []


def test_delete_missing_document_returns_false(kb):
    assert knowledge.delete_document("nope.pdf") is False


def test_delete_cannot_escape_the_directory(kb):
    with pytest.raises(ValueError):
        knowledge.delete_document("../../secrets.py")


# ── Status ──────────────────────────────────────────────────────

def test_status_reports_local_store(kb):
    status = knowledge.index_status()
    assert status["store"] == "local index"
    assert status["pinecone_index"] is None


def test_status_reports_pinecone_when_configured(kb, monkeypatch):
    monkeypatch.setattr(config, "PINECONE_API_KEY", "pc-key")
    monkeypatch.setattr(config, "PINECONE_INDEX", "healthbot-protocols")
    status = knowledge.index_status()
    assert status["store"] == "Pinecone"
    assert status["pinecone_index"] == "healthbot-protocols"


def test_status_counts_documents(kb):
    knowledge.save_upload("a.pdf", PDF_BYTES)
    assert knowledge.index_status()["documents"] == 1


# ── Endpoints ───────────────────────────────────────────────────

def test_knowledge_endpoint_returns_a_document_list(kb):
    knowledge.save_upload("a.pdf", PDF_BYTES)
    body = client.get("/api/knowledge").json()
    assert isinstance(body["documents"], list)   # not a count
    assert body["documents"][0]["name"] == "a.pdf"
    assert "embedding_provider" in body


def test_upload_requires_admin_token(kb):
    r = client.post(
        "/api/knowledge/upload",
        data={"admin_token": "wrong"},
        files={"file": ("a.pdf", PDF_BYTES, "application/pdf")},
    )
    assert r.status_code == 403
    assert knowledge.list_documents() == []


def test_upload_endpoint_accepts_valid_document(kb):
    r = client.post(
        "/api/knowledge/upload",
        data={"admin_token": TOKEN},
        files={"file": ("who_etat.pdf", PDF_BYTES, "application/pdf")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "who_etat.pdf"


def test_upload_endpoint_rejects_bad_type(kb):
    r = client.post(
        "/api/knowledge/upload",
        data={"admin_token": TOKEN},
        files={"file": ("payload.py", b"import os", "text/x-python")},
    )
    assert r.status_code == 400


def test_delete_endpoint_requires_token(kb):
    knowledge.save_upload("a.pdf", PDF_BYTES)
    assert client.post("/api/knowledge/delete", data={"admin_token": "no", "name": "a.pdf"}).status_code == 403
    assert len(knowledge.list_documents()) == 1


def test_delete_endpoint_404s_for_missing(kb):
    r = client.post("/api/knowledge/delete", data={"admin_token": TOKEN, "name": "nope.pdf"})
    assert r.status_code == 404


def test_rebuild_requires_token(kb):
    assert client.post("/api/knowledge/rebuild", data={"admin_token": "no"}).status_code == 403


def test_writes_refused_when_admin_token_unset(kb, monkeypatch):
    """An unconfigured system stays locked, matching the settings page."""
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    r = client.post(
        "/api/knowledge/upload",
        data={"admin_token": ""},
        files={"file": ("a.pdf", PDF_BYTES, "application/pdf")},
    )
    assert r.status_code == 403


def test_preview_endpoint_is_readable_without_a_token(kb, monkeypatch):
    """Seeing what retrieval returns exposes only public protocol text."""
    monkeypatch.setattr(knowledge.rag, "retrieve", lambda q, k=None: [])
    assert client.get("/api/knowledge/preview?q=fever").status_code == 200


def test_preview_shapes_results(kb, monkeypatch):
    from langchain_core.documents import Document

    monkeypatch.setattr(
        knowledge.rag,
        "retrieve",
        lambda q, k=None: [
            Document(page_content="Danger signs include convulsions.",
                     metadata={"source": "data/protocols/who_imci.pdf", "page": 6})
        ],
    )
    hit = knowledge.preview("danger signs")[0]
    assert hit["source"] == "who_imci.pdf"
    assert hit["page"] == 7  # zero-indexed internally, 1-indexed for people
