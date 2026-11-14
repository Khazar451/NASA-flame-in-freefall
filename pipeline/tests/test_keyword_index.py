import argparse

import numpy as np
import pytest

from pipeline import keyword_index as ki

TEXTS = [
    "At 34% oxygen and 8.2 psia the flames generally persisted for minutes.",
    "The flow duct used a variable speed fan; flow restrictors reduced the maximum flow.",
    "References: Smith et al. 2010, Combustion and Flame.",
]


def test_bm25_ranks_the_matching_text_first():
    index = ki.KeywordIndex.build(TEXTS)
    scores = index.scores("fan flow restrictors")
    assert int(np.argmax(scores)) == 1 and scores[0] == 0


def test_synonyms_connect_question_words_to_document_words():
    index = ki.KeywordIndex.build(TEXTS)
    assert index.scores("How does lowering the pressure affect fires?")[0] > 0  # pressure -> psia, fire -> flames


def test_save_and_load_give_the_same_scores(tmp_path):
    index = ki.KeywordIndex.build(TEXTS)
    path = tmp_path / ki.FILE
    index.save(path)
    again = ki.KeywordIndex.load(path)
    assert np.allclose(index.scores("oxygen flames"), again.scores("oxygen flames"))


def test_tokens_drop_stopwords_and_keep_decimals():
    assert ki.tokens("The flames at 8.2 psia and O₂") == ["flames", "8.2", "psia", "o2"]


def test_hybrid_search_merges_both_rankings():
    pytest.importorskip("chromadb")
    from pipeline.search import SearchIndex
    from pipeline.tests.test_build_index import fake_embedder

    chunks = [{"id": f"c{i}", "psi_id": "PSI-20" if i == 0 else "PSI-25", "text": t} for i, t in enumerate(TEXTS)]
    index = SearchIndex(chunks, np.array(fake_embedder(TEXTS)), fake_embedder, ki.KeywordIndex.build(TEXTS))
    top = index.hybrid("flow restrictors fan", k=2)
    assert top[0]["id"] == "c1"
    assert [c["id"] for c in index.hybrid("flames persisted", k=3, psi_ids=["PSI-20"])] == ["c0"]
