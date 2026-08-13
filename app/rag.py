"""RAG pipeline over FMOH/WHO clinical protocols (Sprint 4).

Ingest: PDFs/txt/md in data/protocols → chunk → embed → vector store.
Store: Pinecone when PINECONE_API_KEY is set; otherwise a local
JSON-serialised in-memory store so development works offline.

Retrieval failures never block triage — the conversation simply
proceeds without protocol context.
"""

import logging
import json
import math
from pathlib import Path
from datetime import datetime, timezone

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app import config

log = logging.getLogger(__name__)

_store = None  # lazy singleton, reset after each ingest


def load_documents(docs_dir: str) -> list:
    """One Document per PDF page (with page metadata) or text file."""
    from langchain_core.documents import Document
    from pypdf import PdfReader

    docs = []
    for path in sorted(Path(docs_dir).glob("**/*")):
        if path.suffix.lower() == ".pdf":
            for page_number, page in enumerate(PdfReader(str(path)).pages):
                text = (page.extract_text() or "").strip()
                if text:
                    docs.append(
                        Document(
                            page_content=text,
                            metadata={"source": str(path), "page": page_number},
                        )
                    )
        elif path.suffix.lower() in {".txt", ".md"}:
            docs.append(
                Document(
                    page_content=path.read_text(encoding="utf-8"),
                    metadata={"source": str(path)},
                )
            )
    return docs


def is_usable_chunk(text: str) -> bool:
    """Reject chunks that would pollute clinical retrieval.

    Some official PDFs (notably the WHO IMCI booklet) carry a broken
    font ToUnicode map, so parts extract as a cipher — 'Give an
    Appropriate Oral Antibiotic' becomes '*LYH\\x03DQ\\x03$SSURSULDWH'.
    Those, page-number fragments and bare URL runs carry no clinical
    meaning but can still be retrieved and pasted into the triage
    prompt, so they are dropped at ingest time.
    """
    stripped = text.strip()
    if len(stripped) < 60:
        return False
    control = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    if control / len(text) > 0.01:
        return False
    words = sum(1 for w in text.split() if len(w) > 2 and all(c.isalpha() for c in w))
    return words >= 5


def split_documents(docs: list) -> list:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800, chunk_overlap=120, add_start_index=True
    )
    chunks = splitter.split_documents(docs)
    usable = [c for c in chunks if is_usable_chunk(c.page_content)]
    dropped = len(chunks) - len(usable)
    if dropped:
        log.info("Dropped %d unusable chunks of %d", dropped, len(chunks))
    return usable


def ingest(docs_dir: str | None = None, embeddings=None) -> int:
    """Chunk and embed every protocol document. Returns the chunk count."""
    global _store
    docs_dir = docs_dir or config.PROTOCOLS_DIR
    docs = load_documents(docs_dir)
    if not docs:
        raise FileNotFoundError(f"No .pdf/.txt/.md protocol files found in {docs_dir}")
    chunks = split_documents(docs)
    embeddings = embeddings or _embeddings()

    if config.PINECONE_API_KEY:
        from langchain_pinecone import PineconeVectorStore

        PineconeVectorStore.from_documents(
            chunks, embeddings, index_name=config.PINECONE_INDEX
        )
    else:
        store = InMemoryVectorStore(embeddings)
        store.add_documents(chunks)
        out = Path(config.LOCAL_INDEX_PATH)
        out.parent.mkdir(parents=True, exist_ok=True)
        store.dump(str(out))

    # Sidecar metadata: which embedding provider/model built this index.
    # The dashboard compares it against the current settings so an
    # embedding-provider switch after ingest is surfaced as a warning
    # instead of silently degrading retrieval.
    meta = {
        "embedding_provider": config.EMBEDDING_PROVIDER,
        "embedding_model": (
            config.LOCAL_EMBEDDING_MODEL
            if config.EMBEDDING_PROVIDER == "local"
            else config.HF_EMBEDDING_MODEL
            if config.EMBEDDING_PROVIDER == "hf"
            else config.EMBEDDING_MODEL
        ),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "chunks": len(chunks),
        "documents": len(docs),
    }
    meta_path = Path(str(config.LOCAL_INDEX_PATH) + ".meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    _store = None  # force reload on next retrieve
    return len(chunks)


def index_meta() -> dict | None:
    """The sidecar metadata of the last build, or None when absent."""
    meta_path = Path(str(config.LOCAL_INDEX_PATH) + ".meta.json")
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def reset_store() -> None:
    """Drop the cached vector store so the next retrieval re-initialises
    with the current settings (provider, key, embedding model)."""
    global _store
    _store = None


def warm() -> bool:
    """Load the vector store (and embedding model) ahead of the first
    request. Cold load takes ~15s — longer than Twilio's webhook
    timeout — so the server does this at startup instead of inside a
    patient's first message. Safe to call when no index exists."""
    try:
        ready = _get_store() is not None
        log.info("RAG store %s", "warmed" if ready else "unavailable (running without protocol context)")
        return ready
    except Exception:
        log.warning("RAG warm-up failed; continuing without protocol context")
        return False


def retrieve(query: str, k: int | None = None) -> list:
    """Top-k protocol chunks for the query; empty list on any failure."""
    try:
        store = _get_store()
        if store is None:
            return []
        return store.similarity_search(query, k=k or config.RAG_TOP_K)
    except Exception:
        log.warning("RAG retrieval unavailable; continuing without context")
        return []


def format_context(docs: list) -> str:
    """Render retrieved chunks as a cited block for the system prompt."""
    blocks = []
    for doc in docs:
        source = Path(str(doc.metadata.get("source", "protocol"))).name
        page = doc.metadata.get("page")
        label = source + (f", p.{page + 1}" if isinstance(page, int) else "")
        blocks.append(f"[{label}]\n{doc.page_content.strip()}")
    return "\n\n".join(blocks)


def _embeddings():
    """Embedding client.

    EMBEDDING_PROVIDER=local runs a multilingual sentence-transformer on
    this machine — no API key, no cost, works offline. EMBEDDING_PROVIDER=hf
    runs the same model on Hugging Face's hosted Inference API — same
    vector space, but no torch dependency on the host (cloud deployment).
    Otherwise an OpenAI-style endpoint is used, defaulting to the chat
    key/URL but overridable via EMBEDDING_API_KEY / EMBEDDING_BASE_URL
    (many gateways serve chat models but no embedding models).
    """
    if config.EMBEDDING_PROVIDER == "local":
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(
            model_name=config.LOCAL_EMBEDDING_MODEL,
            encode_kwargs={"normalize_embeddings": True},
        )

    if config.EMBEDDING_PROVIDER == "hf":
        return HfInferenceEmbeddings(
            model=config.HF_EMBEDDING_MODEL,
            token=config.HF_API_TOKEN,
        )

    from langchain_openai import OpenAIEmbeddings

    api_key = config.EMBEDDING_API_KEY or config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    kwargs = {"model": config.EMBEDDING_MODEL, "api_key": api_key}
    base_url = config.EMBEDDING_BASE_URL or config.OPENAI_BASE_URL
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIEmbeddings(**kwargs)


class HfInferenceEmbeddings(Embeddings):
    """LangChain-compatible embeddings over the Hugging Face Inference API.

    Serves the SAME sentence-transformer models the local provider runs,
    so an index built locally (384-dim, L2-normalised MiniLM) stays valid
    on a host without torch. Vectors are L2-normalised client-side to
    match the local provider's normalize_embeddings=True.
    """

    def __init__(self, model: str, token: str, timeout: float = 12.0,
                 batch_size: int = 16):
        if not token:
            raise RuntimeError("HF_API_TOKEN is not set")
        self.model = model
        self.token = token
        self.timeout = timeout
        self.batch_size = batch_size

    def _embed(self, texts: list) -> list:
        if not texts:
            return []
        url = (
            "https://router.huggingface.co/hf-inference/models/"
            f"{self.model}"
        )
        headers = {"Authorization": f"Bearer {self.token}"}
        out: list = []
        # HF caps inputs per request; batch so ingests and query bursts
        # stay under the limit and rate limits are hit gently.
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            payload = {"inputs": batch, "wait_for_model": True}
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPError as exc:
                log.warning("HF embeddings call failed: %s", exc)
                raise RuntimeError("HF embeddings unavailable") from exc
            # HF returns a list of vectors for batches, or a bare vector
            # when the caller sent a single text — normalise both shapes.
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected HF embeddings response: {type(data)}")
            vectors = [data] if (data and isinstance(data[0], float)) else data
            if len(vectors) != len(batch):
                raise RuntimeError(
                    f"HF returned {len(vectors)} vectors for {len(batch)} inputs"
                )
            out.extend(self._normalise(v) for v in vectors)
        return out

    @staticmethod
    def _normalise(vector: list) -> list:
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            return vector
        return [x / norm for x in vector]

    def embed_documents(self, texts: list) -> list:
        return self._embed(list(texts))

    def embed_query(self, text: str) -> list:
        return self._embed([text])[0]


def _get_store(embeddings=None):
    global _store
    if _store is not None:
        return _store
    if config.PINECONE_API_KEY:
        from langchain_pinecone import PineconeVectorStore

        _store = PineconeVectorStore(
            index_name=config.PINECONE_INDEX,
            embedding=embeddings or _embeddings(),
        )
    elif Path(config.LOCAL_INDEX_PATH).exists():
        _store = InMemoryVectorStore.load(
            config.LOCAL_INDEX_PATH, embeddings or _embeddings()
        )
    return _store
