import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from app import config, conversation, rag

FEVER_LINE = "Fever lasting more than 24 hours in a child under 2 years needs same-day assessment."
SNAKE_LINE = "A snake bite victim should be kept still and taken to a facility with antivenom."


@pytest.fixture
def local_index(tmp_path, monkeypatch):
    """Two tiny protocol docs ingested into a local index with fake embeddings."""
    docs_dir = tmp_path / "protocols"
    docs_dir.mkdir()
    (docs_dir / "fever.txt").write_text(FEVER_LINE, encoding="utf-8")
    (docs_dir / "snakebite.txt").write_text(SNAKE_LINE, encoding="utf-8")

    fake = DeterministicFakeEmbedding(size=64)
    monkeypatch.setattr(config, "PINECONE_API_KEY", "")
    monkeypatch.setattr(config, "LOCAL_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setattr(rag, "_embeddings", lambda: fake)
    monkeypatch.setattr(rag, "_store", None)
    yield docs_dir
    rag._store = None


def test_ingest_builds_local_index(local_index, tmp_path):
    count = rag.ingest(str(local_index))
    assert count == 2
    assert (tmp_path / "index.json").exists()


def test_ingest_with_no_documents_raises(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        rag.ingest(str(empty))


def test_retrieve_finds_matching_chunk(local_index):
    rag.ingest(str(local_index))
    # Deterministic embeddings: identical text → identical vector → top hit.
    results = rag.retrieve(FEVER_LINE, k=1)
    assert len(results) == 1
    assert results[0].page_content == FEVER_LINE
    assert "fever.txt" in results[0].metadata["source"]


def test_retrieve_without_index_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PINECONE_API_KEY", "")
    monkeypatch.setattr(config, "LOCAL_INDEX_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(rag, "_store", None)
    assert rag.retrieve("child fever") == []


def test_retrieve_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("embeddings down")

    monkeypatch.setattr(rag, "_embeddings", _boom)
    monkeypatch.setattr(rag, "_store", None)
    assert rag.retrieve("anything") == []


def test_rejects_font_cipher_garbage():
    # Real sample from the WHO IMCI booklet's broken ToUnicode map.
    cipher = "*LYH\x03DQ\x03$SSURSULDWH\x032UDO\x03$QWLELRWLF \x14\x15\n*LYH\x03,QKDOHG\x036DOEXWDPRO"
    assert not rag.is_usable_chunk(cipher)


def test_rejects_too_short_and_wordless_chunks():
    assert not rag.is_usable_chunk("1  2  3  4")
    assert not rag.is_usable_chunk("")
    assert not rag.is_usable_chunk(
        "pdf;jsessionid=862B3C6054CED65E30EDE6605FFAEDF4?sequence=1 ICD10: http://x.y"
    )


def test_keeps_real_clinical_text_including_doses():
    assert rag.is_usable_chunk(
        "A child with any general danger sign needs urgent attention and referral "
        "to hospital immediately for further assessment."
    )
    assert rag.is_usable_chunk(
        "The recommended dose is 15-20mg/kg. At this stage, a senior health worker "
        "should review the child before any further treatment is given."
    )


def test_split_documents_filters_garbage():
    from langchain_core.documents import Document

    docs = [
        Document(page_content="A" * 5 + "\x03" * 200, metadata={"source": "bad.pdf"}),
        Document(
            page_content=(
                "Fever lasting more than 24 hours in a child under two years should "
                "be assessed by a health worker on the same day without delay."
            ),
            metadata={"source": "good.pdf"},
        ),
    ]
    chunks = rag.split_documents(docs)
    assert len(chunks) == 1
    assert "good.pdf" in chunks[0].metadata["source"]


def test_format_context_cites_source_and_page():
    docs = [
        Document(
            page_content="Fast breathing means same-day clinic assessment.",
            metadata={"source": "data/protocols/who_imci.pdf", "page": 4},
        )
    ]
    context = rag.format_context(docs)
    assert "[who_imci.pdf, p.5]" in context
    assert "Fast breathing" in context


def test_loader_reads_pdf_and_text(tmp_path):
    from pypdf import PdfWriter

    pdf_path = tmp_path / "protocol.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf_path, "wb") as f:
        writer.write(f)
    (tmp_path / "notes.txt").write_text("Give ORS for diarrhoea.", encoding="utf-8")

    docs = rag.load_documents(str(tmp_path))
    # Blank PDF page has no extractable text → skipped, not indexed as noise.
    assert len(docs) == 1
    assert docs[0].page_content == "Give ORS for diarrhoea."
    assert "notes.txt" in docs[0].metadata["source"]


def test_conversation_grounds_prompt_in_retrieved_protocols(fake_llm, monkeypatch):
    chunk = Document(
        page_content="Convulsions are a general danger sign requiring urgent referral.",
        metadata={"source": "who_imci.pdf", "page": 2},
    )
    monkeypatch.setattr(rag, "retrieve", lambda q, k=None: [chunk])

    conversation.handle_message("whatsapp:+2348011111111", "my pikin body dey hot")
    system_message = fake_llm[0][0]
    assert system_message["role"] == "system"
    assert "general danger sign" in system_message["content"]
    assert "[who_imci.pdf, p.3]" in system_message["content"]
    assert "never justify diagnosing or prescribing" in system_message["content"]


def test_conversation_works_without_rag_context(fake_llm, monkeypatch):
    monkeypatch.setattr(rag, "retrieve", lambda q, k=None: [])
    reply = conversation.handle_message("whatsapp:+2348011111111", "I get small headache")
    assert reply == "How old is the child?"  # canned fake reply
    assert fake_llm[0][0]["content"] == conversation.SYSTEM_PROMPT


def test_warm_returns_false_without_index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PINECONE_API_KEY", "")
    monkeypatch.setattr(config, "LOCAL_INDEX_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(rag, "_store", None)
    assert rag.warm() is False


def test_warm_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("embeddings unavailable")

    monkeypatch.setattr(rag, "_embeddings", _boom)
    monkeypatch.setattr(rag, "_store", None)
    assert rag.warm() is False


def test_warm_loads_existing_index(local_index, monkeypatch):
    rag.ingest(str(local_index))
    monkeypatch.setattr(rag, "_store", None)
    assert rag.warm() is True


def test_startup_warms_rag_in_background(monkeypatch):
    """The webhook must not pay the ~15s cold-load cost (Twilio times
    out at 15s), so startup kicks off warming in a thread."""
    from fastapi.testclient import TestClient

    called = []
    monkeypatch.setattr(rag, "warm", lambda: called.append(True))
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        client.get("/health")
    assert called, "startup did not warm the RAG store"
