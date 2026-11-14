"""Document search for /ask: the committed index loaded into an in-memory Chroma collection.

    python -m pipeline.search --download-model    fetch the embedding model now, e.g. during a
                                                   server build, so the first question is not slow
"""

from __future__ import annotations

import argparse
import threading
import uuid
from pathlib import Path
from typing import Any, Sequence

from pipeline import build_index


class SearchIndex:
    """Chunks from data/out/search/ in a Chroma collection; questions are embedded on demand."""

    def __init__(self, chunks: list[dict[str, Any]], vectors, embedder: build_index.Embedder) -> None:
        import chromadb
        from chromadb.config import Settings

        self.chunks = {c["id"]: c for c in chunks}
        self._embedder = embedder
        # Chroma creates its model lazily, and that is not thread-safe: questions arriving together
        # each loaded their own copy (8 at once used 1.3 GB). So load it now, once, and let one
        # question at a time use it; an embedding takes milliseconds.
        self._lock = threading.Lock()
        self._embedder(["warm up"])
        client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
        # A fresh name per load: in-memory clients in one process share their collections.
        self._collection = client.create_collection(
            name=f"psi-{uuid.uuid4().hex[:12]}", embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
        for start in range(0, len(chunks), 4000):  # Chroma limits the size of one add
            batch = chunks[start:start + 4000]
            self._collection.add(
                ids=[c["id"] for c in batch],
                # A NumPy array, not Python lists: lists of floats would briefly need ~120 MB more,
                # which matters on a 512 MB host.
                embeddings=vectors[start:start + 4000],
                metadatas=[{"psi_id": c["psi_id"]} for c in batch],
            )

    @classmethod
    def load(cls, folder: Path | None = None, embedder: build_index.Embedder | None = None) -> "SearchIndex":
        chunks, vectors, _ = build_index.load(folder or build_index.SEARCH_DIR)
        return cls(chunks, vectors, embedder or build_index.default_embedder())

    def query(self, text: str, k: int = 6, psi_ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """The k chunks closest in meaning to `text`, optionally only from some investigations."""
        with self._lock:
            vector = [float(x) for x in self._embedder([text])[0]]
        where = {"psi_id": {"$in": list(psi_ids)}} if psi_ids else None
        result = self._collection.query(
            query_embeddings=[vector], n_results=min(k, len(self.chunks)), where=where,
            include=["distances"],
        )
        return [
            {**self.chunks[chunk_id], "score": round(1 - distance, 4)}
            for chunk_id, distance in zip(result["ids"][0], result["distances"][0])
        ]


def download_model() -> int:
    """Download the embedding model (if it is not there yet) and embed one sentence to prove it works."""
    vector = build_index.default_embedder()(["warm up"])[0]
    print(f"search: embedding model ready ({len(vector)} dimensions)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.search")
    parser.add_argument("--download-model", action="store_true", help="fetch the embedding model now")
    args = parser.parse_args(argv)
    if args.download_model:
        return download_model()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
