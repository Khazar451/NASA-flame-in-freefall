import argparse
import hashlib
import json
import math

import pytest

from pipeline import build_index, extract_text


def fake_embedder(texts):
    """Deterministic bag-of-words vectors: texts sharing words point the same way."""
    out = []
    for text in texts:
        vector = [0.0] * 32
        for word in text.lower().split():
            vector[int(hashlib.md5(word.strip(".,").encode()).hexdigest(), 16) % 32] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        out.append([v / norm for v in vector])
    return out


def test_split_text_overlaps_and_respects_the_size():
    text = " ".join(f"w{i:04d}" for i in range(1000))
    parts = build_index.split_text(text, 1500, 200)
    assert all(len(p) <= 1500 for p in parts) and len(parts) == 5
    for left, right in zip(parts, parts[1:]):
        assert right.split()[0] in left  # each piece starts inside the previous one
    assert " ".join(parts[-1].split()[-1:]) == "w0999"
    assert build_index.split_text("  short   text ") == ["short text"]
    assert build_index.split_text("   ") == []


@pytest.fixture
def text_dir(tmp_path, monkeypatch):
    folder = tmp_path / "text"
    folder.mkdir()
    monkeypatch.setattr(extract_text, "TEXT_DIR", folder)
    monkeypatch.setattr(extract_text, "INDEX", folder / "index.json")
    pages = {
        "PSI-69_Reports_FLEX: Final.pdf": [
            "Carbon dioxide suppressed methanol droplet flames in microgravity tests aboard the ISS. Next line.",
            "7",
            "Helium dilution changed the extinction diameter of heptane droplets at low pressure.",
        ],
        "PSI-25_Reports_Scan.pdf": ["x"],
    }
    index = {}
    for source_id, texts in pages.items():
        psi_id = source_id.split("_")[0]
        lines = [{"source_id": source_id, "psi_id": psi_id, "category": "Reports", "page": n, "text": t}
                 for n, t in enumerate(texts, start=1)]
        name = build_index.fetch.local_name(source_id)
        (folder / f"{name}.jsonl").write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n")
        index[source_id] = {"pages": len(texts), "chars": 1, "psi_id": psi_id, "category": "Reports",
                            "scanned": "Scan" in source_id}
    record = [{"source_id": "PSI-25_record", "psi_id": "PSI-25", "category": "PSI record", "page": None,
               "section": "objectives", "text": "Study flame spread over thin solid fabric samples in a small wind tunnel inside the glovebox."}]
    (folder / "PSI-25_record.jsonl").write_text(json.dumps(record[0]) + "\n")
    index["PSI-25_record"] = {"pages": 1, "chars": 1, "psi_id": "PSI-25", "category": "PSI record"}
    index["PSI-22_Broken.pdf"] = {"pages": 0, "chars": 0, "psi_id": "PSI-22", "category": "Reports", "error": "bad"}
    (folder / "index.json").write_text(json.dumps(index))
    return folder


def test_chunks_skip_scanned_unreadable_and_near_empty_pages(text_dir):
    chunks, skipped = build_index.build_chunks(json.loads((text_dir / "index.json").read_text()))
    assert skipped == {"scanned_sources": 1, "unreadable_sources": 1, "near_empty_pages": 1}
    assert [(c["source_id"], c["page"], c["section"]) for c in chunks] == [
        ("PSI-25_record", None, "objectives"),
        ("PSI-69_Reports_FLEX: Final.pdf", 1, None),
        ("PSI-69_Reports_FLEX: Final.pdf", 3, None),
    ]
    assert chunks[1]["text"].endswith("ISS. Next line.")  # an odd Unicode line break did not split the line
    assert [c["id"] for c in chunks] == ["c0", "c1", "c2"]


def test_build_save_load_and_search(text_dir, tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from pipeline.search import SearchIndex

    out = tmp_path / "search"
    monkeypatch.setattr(build_index, "SEARCH_DIR", out)
    monkeypatch.setattr(build_index.config, "REPO_ROOT", tmp_path)
    assert build_index.run(argparse.Namespace(), embedder=fake_embedder) == 0
    chunks, vectors, manifest = build_index.load(out)
    assert manifest["chunks"] == 3 and manifest["dimensions"] == 32 and vectors.shape == (3, 32)

    index = SearchIndex(chunks, vectors, fake_embedder)
    top = index.query("helium dilution extinction diameter heptane", k=1)
    assert top[0]["page"] == 3 and 0 < top[0]["score"] <= 1
    only_bass = index.query("helium dilution extinction diameter heptane", k=3, psi_ids=["PSI-25"])
    assert [c["source_id"] for c in only_bass] == ["PSI-25_record"]
    second = SearchIndex(chunks, vectors, fake_embedder)  # two indexes in one process do not clash
    assert len(second.query("carbon dioxide", k=3)) == 3


def test_build_index_needs_extracted_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(extract_text, "INDEX", tmp_path / "missing.json")
    assert build_index.run(argparse.Namespace(), embedder=fake_embedder) == 1
    assert "run extract-text first" in capsys.readouterr().out


def test_search_loads_the_model_once_and_embeds_one_question_at_a_time():
    pytest.importorskip("chromadb")
    import threading
    import time

    from pipeline.search import SearchIndex

    calls, active, overlap = [], [0], [False]

    def slow_embedder(texts):
        active[0] += 1
        overlap[0] |= active[0] > 1
        time.sleep(0.01)
        active[0] -= 1
        calls.append(list(texts))
        return fake_embedder(texts)

    chunks = [{"id": "c0", "psi_id": "PSI-69", "text": "carbon dioxide"}]
    index = SearchIndex(chunks, __import__("numpy").array(fake_embedder(["carbon dioxide"])), slow_embedder)
    assert calls == [["warm up"]]  # loaded when the index is created, before any question
    threads = [threading.Thread(target=index.query, args=("carbon dioxide",)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 7 and not overlap[0]
