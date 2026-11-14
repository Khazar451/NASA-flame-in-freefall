"""Phase 7: build the document search index used by /ask.

Every page of every readable document (and every section of each PSI record) becomes
one or more chunks of at most CHUNK_CHARS characters, cut at word boundaries with
OVERLAP characters repeated between neighbours. Scanned and unreadable documents are
skipped, as are near-empty pages.

Chunks are embedded with Chroma's default embedding model (all-MiniLM-L6-v2, which
runs locally, so it costs no AI quota) and saved to data/out/search/:
  chunks.jsonl.gz   one chunk per line: id, source_id, psi_id, category, page, section, text
  embeddings.npy    float16 vectors, one row per chunk, in the same order
  manifest.json     model, dimensions, counts, chunking settings
These files are committed, so the /ask server can start from them without the raw
documents and without embedding anything except the questions it receives.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from pipeline import config, extract_text, fetch

SEARCH_DIR = config.OUT_DIR / "search"
CHUNKS = "chunks.jsonl.gz"
EMBEDDINGS = "embeddings.npy"
MANIFEST = "manifest.json"
CHUNK_CHARS = 1500
OVERLAP = 200
MIN_CHARS = 80  # page numbers, blank slides and figure captions on their own
MODEL_NAME = "all-MiniLM-L6-v2"
BATCH = 128

Embedder = Callable[[Sequence[str]], Any]  # texts -> sequence of vectors


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Cut text into pieces of at most `size` characters at spaces, overlapping by about `overlap`."""
    # NFKC turns PDF ligatures such as "ﬂ" back into plain letters ("fl").
    text = " ".join(unicodedata.normalize("NFKC", text).split())
    if len(text) <= size:
        return [text] if text else []
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            end = cut if cut > start else end
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = text.find(" ", max(end - overlap, start + 1))
        start = next_start + 1 if 0 <= next_start < end else end
    return [p for p in pieces if p]


def text_file(source_id: str, info: dict[str, Any]) -> Path:
    """Where extract-text wrote a source's lines (names are made safe for every OS)."""
    name = source_id if info["category"] == "PSI record" else fetch.local_name(source_id)
    return extract_text.TEXT_DIR / f"{name}.jsonl"


def read_lines(path: Path) -> list[dict[str, Any]]:
    # Split on "\n" only: the text can contain other Unicode line breaks.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]


def build_chunks(index: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """All chunks in a stable order, plus counts of what was skipped."""
    chunks: list[dict[str, Any]] = []
    skipped = {"scanned_sources": 0, "unreadable_sources": 0, "near_empty_pages": 0}
    for source_id in sorted(index):
        info = index[source_id]
        if "error" in info:
            skipped["unreadable_sources"] += 1
            continue
        if info.get("scanned"):
            skipped["scanned_sources"] += 1
            continue
        for line in read_lines(text_file(source_id, info)):
            if len(line["text"].strip()) < MIN_CHARS:
                skipped["near_empty_pages"] += 1
                continue
            for part, text in enumerate(split_text(line["text"]), start=1):
                chunks.append({
                    "id": f"c{len(chunks)}",
                    "source_id": source_id,
                    "psi_id": line["psi_id"],
                    "category": line["category"],
                    "page": line.get("page"),
                    "section": line.get("section"),
                    "part": part,
                    "text": text,
                })
    return chunks, skipped


def default_embedder() -> Embedder:
    """Chroma's built-in model. The first call downloads it (about 80 MB) to ~/.cache/chroma."""
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()


def embed(texts: Sequence[str], embedder: Embedder, report: Callable[[int], None] | None = None):
    import numpy as np

    rows = []
    for start in range(0, len(texts), BATCH):
        rows.append(np.asarray(embedder(list(texts[start:start + BATCH])), dtype=np.float32))
        if report:
            report(min(start + BATCH, len(texts)))
    return np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.float32)


def save(folder: Path, chunks: list[dict[str, Any]], vectors, model: str, skipped: dict[str, int]) -> None:
    import numpy as np

    folder.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the gzip bytes identical when the content is identical.
    with open(folder / CHUNKS, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        for chunk in chunks:
            fh.write((json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8"))
    np.save(folder / EMBEDDINGS, vectors.astype(np.float16))
    manifest = {
        "model": model,
        "dimensions": int(vectors.shape[1]) if len(vectors) else 0,
        "chunks": len(chunks),
        "sources": len({c["source_id"] for c in chunks}),
        "investigations": len({c["psi_id"] for c in chunks}),
        "chunk_chars": CHUNK_CHARS,
        "overlap": OVERLAP,
        "skipped": skipped,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def load(folder: Path) -> tuple[list[dict[str, Any]], Any, dict[str, Any]]:
    import numpy as np

    with gzip.open(folder / CHUNKS, "rt", encoding="utf-8") as fh:
        chunks = [json.loads(line) for line in fh.read().split("\n") if line]
    vectors = np.load(folder / EMBEDDINGS).astype(np.float32)
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    if len(chunks) != len(vectors):
        raise ValueError(f"{folder}: {len(chunks)} chunks but {len(vectors)} vectors; rebuild the index")
    return chunks, vectors, manifest


def run(args: argparse.Namespace, embedder: Embedder | None = None) -> int:
    if not extract_text.INDEX.exists():
        print("build-index: no extracted text; run extract-text first.")
        return 1
    index = json.loads(extract_text.INDEX.read_text(encoding="utf-8"))
    chunks, skipped = build_chunks(index)
    print(f"build-index: {len(chunks)} chunks from {len({c['source_id'] for c in chunks})} sources; skipped {skipped}")
    embedder = embedder or default_embedder()
    started = time.monotonic()
    last = [0.0]

    def report(done: int) -> None:
        if time.monotonic() - last[0] > 20 or done == len(chunks):
            last[0] = time.monotonic()
            print(f"  embedded {done}/{len(chunks)} ({time.monotonic() - started:.0f} s)", flush=True)

    vectors = embed([c["text"] for c in chunks], embedder, report)
    save(SEARCH_DIR, chunks, vectors, MODEL_NAME if embedder.__class__.__name__ == "DefaultEmbeddingFunction"
         else embedder.__class__.__name__, skipped)
    sizes = ", ".join(f"{p.name} {p.stat().st_size / 1e6:.1f} MB" for p in sorted(SEARCH_DIR.iterdir()))
    print(f"Wrote {SEARCH_DIR.relative_to(config.REPO_ROOT)}: {sizes}")
    return 0
