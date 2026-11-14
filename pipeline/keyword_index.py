"""Keyword (BM25) search over the same chunks as the meaning-based Chroma index.

Meaning-based search alone misses passages whose wording is close to the question's
("pressure", "flammability", "psia") when a small embedding model ranks other text
higher. /ask combines both rankings.

The index is a few compact NumPy arrays (about 10 MB), built once from
data/out/search/chunks.jsonl.gz and saved as data/out/search/keywords.npz:

    python -m pipeline.keyword_index      rebuild it from the saved chunks
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Sequence

from pipeline import build_index, config

FILE = "keywords.npz"
K1, B = 1.2, 0.75
STOPWORDS = set("""a an and are as at be been but by can could did do does for from had has have how if in into
is it its may more most no not of on or our so such than that the their them then there these they this to
was we were what when where which while who why will with would you your about also any each other some""".split())
TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
# Words a question uses and the words NASA's documents use for the same thing.
SYNONYMS = {
    "pressure": "psia psi atm kpa", "pressures": "psia psi atm kpa", "lower": "reduced", "lowering": "reduced",
    "flammability": "flammable burning burn extinction ignition", "flammable": "flammability burning",
    "fire": "flame burning", "fires": "flames burning", "dangerous": "flammability burning persisted",
    "oxygen": "o2", "o2": "oxygen", "co2": "carbon dioxide", "suppressant": "suppression extinction",
    "lunar": "moon reduced pressure", "moon": "lunar", "smoke": "particle aerosol", "find": "found results",
    "found": "results showed",
}


def expand(query: str) -> str:
    """The query plus the words NASA's documents use for its terms (keyword search only)."""
    extra = [SYNONYMS[w] for w in re.findall(r"[a-z0-9]+", query.lower()) if w in SYNONYMS]
    return " ".join([query] + extra)


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower().replace("o₂", "o2").replace("co₂", "co2")
    return [t for t in TOKEN.findall(text) if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]


class KeywordIndex:
    def __init__(self, vocab: list[str], term_ptr, doc_ids, tfs, doc_len) -> None:
        import numpy as np

        self.term_of = {term: i for i, term in enumerate(vocab)}
        self.term_ptr, self.doc_ids, self.tfs = term_ptr, doc_ids, tfs.astype(np.float32)
        self.doc_len = doc_len.astype(np.float32)
        self.avg_len = float(self.doc_len.mean()) if len(self.doc_len) else 1.0
        self.n_docs = len(self.doc_len)

    @classmethod
    def build(cls, texts: Sequence[str]) -> "KeywordIndex":
        import numpy as np

        postings: dict[str, list[tuple[int, int]]] = {}
        doc_len = np.zeros(len(texts), dtype=np.int32)
        for doc, text in enumerate(texts):
            counts = Counter(tokens(text))
            doc_len[doc] = sum(counts.values())
            for term, tf in counts.items():
                postings.setdefault(term, []).append((doc, tf))
        vocab = sorted(postings)
        term_ptr = np.zeros(len(vocab) + 1, dtype=np.int64)
        for i, term in enumerate(vocab):
            term_ptr[i + 1] = term_ptr[i] + len(postings[term])
        doc_ids = np.empty(term_ptr[-1], dtype=np.int32)
        tfs = np.empty(term_ptr[-1], dtype=np.uint16)
        for i, term in enumerate(vocab):
            pairs = postings[term]
            doc_ids[term_ptr[i]:term_ptr[i + 1]] = [d for d, _ in pairs]
            tfs[term_ptr[i]:term_ptr[i + 1]] = [min(tf, 65535) for _, tf in pairs]
        return cls(vocab, term_ptr, doc_ids, tfs, doc_len)

    def save(self, path: Path) -> None:
        import numpy as np

        vocab = sorted(self.term_of, key=self.term_of.get)
        np.savez_compressed(path, vocab=np.array(json.dumps(vocab)), term_ptr=self.term_ptr,
                            doc_ids=self.doc_ids, tfs=self.tfs.astype(np.uint16), doc_len=self.doc_len.astype(np.int32))

    @classmethod
    def load(cls, path: Path) -> "KeywordIndex":
        import numpy as np

        with np.load(path) as data:
            return cls(json.loads(str(data["vocab"])), data["term_ptr"], data["doc_ids"], data["tfs"], data["doc_len"])

    def scores(self, query: str):
        """BM25 score of every document for the query."""
        import numpy as np

        total = np.zeros(self.n_docs, dtype=np.float32)
        for term in set(tokens(expand(query))):
            i = self.term_of.get(term)
            if i is None:
                continue
            start, end = self.term_ptr[i], self.term_ptr[i + 1]
            docs, tf = self.doc_ids[start:end], self.tfs[start:end]
            idf = np.log(1 + (self.n_docs - len(docs) + 0.5) / (len(docs) + 0.5))
            norm = tf + K1 * (1 - B + B * self.doc_len[docs] / self.avg_len)
            total[docs] += idf * tf * (K1 + 1) / norm
        return total


def main() -> int:
    chunks, _, _ = build_index.load(build_index.SEARCH_DIR)
    index = KeywordIndex.build([c["text"] for c in chunks])
    path = build_index.SEARCH_DIR / FILE
    index.save(path)
    print(f"keyword index: {len(index.term_of)} terms over {index.n_docs} chunks, {path.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
