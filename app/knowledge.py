"""Knowledge-base management: the protocol documents behind RAG.

The clinical corpus is the system's authority — what it retrieves is
what it grounds triage guidance in. Adding a document therefore changes
clinical behaviour, which is why uploads are admin-gated, restricted to
document formats, and logged.

Rebuilding the index re-embeds every chunk and takes minutes, so it runs
in a background thread; retrieval keeps serving the previous index until
the new one is ready rather than going dark mid-rebuild.
"""

import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import config, rag

log = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # a chart booklet is ~5 MB

# Rebuild state, so the console can show progress rather than appearing
# to hang for several minutes.
_status: dict = {"running": False, "started": None, "finished": None, "error": None, "chunks": None}
_lock = threading.Lock()


def safe_name(filename: str) -> str:
    """A filename that cannot escape the protocols directory.

    Uploads are admin-gated, but a path-traversal bug would let a
    compromised token write anywhere the process can reach — so the name
    is rebuilt from scratch rather than sanitised in place.
    """
    stem = Path(filename or "").name  # strips any directory component
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", stem).lstrip(".")
    if not cleaned:
        raise ValueError("Filename is empty after sanitising")
    suffix = Path(cleaned).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"Only {', '.join(sorted(ALLOWED_SUFFIXES))} files are accepted, got {suffix or 'no extension'}"
        )
    return cleaned


def _looks_like_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def protocols_dir() -> Path:
    path = Path(config.PROTOCOLS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_documents() -> list[dict]:
    """Every protocol document, with how much of the corpus it supplies."""
    docs = []
    for path in sorted(protocols_dir().glob("*")):
        if path.suffix.lower() not in ALLOWED_SUFFIXES or not path.is_file():
            continue
        stat = path.stat()
        docs.append(
            {
                "name": path.name,
                "size_mb": round(stat.st_size / 1e6, 2),
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "type": path.suffix.lstrip(".").upper(),
            }
        )
    return docs


def index_status() -> dict:
    """What the retrieval layer is actually serving right now."""
    index_path = Path(config.LOCAL_INDEX_PATH)
    using_pinecone = bool(config.PINECONE_API_KEY)
    built_at = None
    size_mb = None
    meta = rag.index_meta()
    if index_path.exists():
        stat = index_path.stat()
        built_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        size_mb = round(stat.st_size / 1e6, 2)

    with _lock:
        rebuild = dict(_status)

    current_model = (
        config.LOCAL_EMBEDDING_MODEL
        if config.EMBEDDING_PROVIDER == "local"
        else config.EMBEDDING_MODEL
    )
    return {
        "store": "Pinecone" if using_pinecone else "local index",
        "pinecone_index": config.PINECONE_INDEX if using_pinecone else None,
        "embedding_provider": config.EMBEDDING_PROVIDER,
        "embedding_model": current_model,
        "index_embedding_provider": (meta or {}).get("embedding_provider"),
        "index_embedding_model": (meta or {}).get("embedding_model"),
        "index_chunks": (meta or {}).get("chunks"),
        # True when the index was built with the same embedding setup that
        # is active now — otherwise retrieval compares incompatible vectors.
        "index_matches_config": bool(
            meta
            and meta.get("embedding_provider") == config.EMBEDDING_PROVIDER
            and meta.get("embedding_model") == current_model
        ),
        "index_built_at": built_at,
        "index_size_mb": size_mb,
        "ready": rag._store is not None or bool(built_at) or using_pinecone,
        "documents": len(list_documents()),
        "rebuild": rebuild,
    }


def save_upload(filename: str, data: bytes) -> dict:
    """Store an uploaded protocol document. Does not rebuild the index —
    the caller decides when, since rebuilding costs minutes."""
    name = safe_name(filename)
    if not data:
        raise ValueError("File is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File is {len(data) / 1e6:.1f} MB; the limit is {MAX_UPLOAD_BYTES / 1e6:.0f} MB"
        )
    # Trust the content, not the extension: a mislabelled file would
    # silently contribute nothing to the corpus.
    if name.lower().endswith(".pdf") and not _looks_like_pdf(data):
        raise ValueError("That file is named .pdf but is not a PDF")

    target = protocols_dir() / name
    replaced = target.exists()
    target.write_bytes(data)
    log.info("Protocol document %s (%s)", "replaced" if replaced else "added", name)
    return {"name": name, "size_mb": round(len(data) / 1e6, 2), "replaced": replaced}


def delete_document(filename: str) -> bool:
    """Remove a protocol document. Returns False if it was not there."""
    name = safe_name(filename)
    target = protocols_dir() / name
    if not target.exists():
        return False
    target.unlink()
    log.info("Protocol document removed: %s", name)
    return True


def rebuild_async() -> dict:
    """Re-ingest every document in the background.

    Retrieval keeps serving the existing index while this runs — a
    clinical system should not lose its protocol grounding for the
    minutes an embedding pass takes.
    """
    with _lock:
        if _status["running"]:
            return {"started": False, "reason": "A rebuild is already running"}
        _status.update(
            running=True,
            started=datetime.now(timezone.utc).isoformat(),
            finished=None,
            error=None,
            chunks=None,
        )

    def _run() -> None:
        try:
            count = rag.ingest()
            with _lock:
                _status.update(running=False, finished=datetime.now(timezone.utc).isoformat(), chunks=count)
            log.info("Knowledge base rebuilt: %d chunks", count)
        except Exception as exc:  # surfaced in the console, not swallowed
            with _lock:
                _status.update(
                    running=False,
                    finished=datetime.now(timezone.utc).isoformat(),
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            log.warning("Knowledge base rebuild failed", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True}


def preview(query: str, k: int = 4) -> list[dict]:
    """What the engine would retrieve for a question — so an
    administrator can see whether a new document is actually reachable
    before trusting it in triage."""
    return [
        {
            "source": Path(str(d.metadata.get("source", "?"))).name,
            "page": (d.metadata.get("page") + 1) if isinstance(d.metadata.get("page"), int) else None,
            "text": d.page_content[:400],
        }
        for d in rag.retrieve(query, k=k)
    ]
