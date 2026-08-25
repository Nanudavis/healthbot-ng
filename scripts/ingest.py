"""Ingest FMOH/WHO protocol documents into the vector store.

Usage:
    .venv/bin/python -m scripts.ingest [docs_dir]

Uses Pinecone when PINECONE_API_KEY is set in .env, otherwise writes a
local JSON index. Configure the selected embedding provider and any required
credentials in .env.
"""

import sys

from app import config, rag

if __name__ == "__main__":
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else config.PROTOCOLS_DIR
    count = rag.ingest(docs_dir)
    target = (
        f"Pinecone index '{config.PINECONE_INDEX}'"
        if config.PINECONE_API_KEY
        else config.LOCAL_INDEX_PATH
    )
    print(f"Ingested {count} chunks from {docs_dir} into {target}")
