import json

import pytest

from pipeline import findings, llm

PAGE = {"source_id": "PSI-20_Reports_Final.pdf", "page": 18, "section": None,
        "text": "At conditions of 27% oxygen, 10.2 psia and 34% oxygen, 8.2 psia, the \ufb02ames generally "
                "persisted for 3 minutes or more."}
INV = {"id": "PSI-20", "acronym": "ACME BRE", "title": "BRE", "url": "https://psi.nasa.gov/x/PSI-20"}


def item(finding, quote, label="P1"):
    return {"finding": finding, "quote": quote, "page_label": label}


@pytest.mark.parametrize("candidate, problem", [
    (item("Flames persisted at 34% oxygen and 8.2 psia.", "34% oxygen, 8.2 psia, the flames generally persisted"), None),
    (item("Flames persisted at 40% oxygen.", "34% oxygen, 8.2 psia, the flames generally persisted"), "numbers not in the quote"),
    (item("Flames went out at 34% oxygen.", "34% oxygen, the flames always went out"), "quote not on the page"),
    (item("Flames persisted.", "27% oxygen ... flames generally persisted"), "quote not on the page"),
])
def test_findings_are_checked_against_their_page(candidate, problem):
    why = findings.check(candidate, PAGE)
    assert (why is None) if problem is None else (problem in why)


@pytest.mark.parametrize("label", ["P1", "[P1]", "1", "page P1"])
def test_page_labels_in_any_format(label):
    reply = {"findings": [item("Flames persisted at 34% oxygen and 8.2 psia.",
                               "34% oxygen, 8.2 psia, the flames generally persisted", label)]}
    provider = llm.FakeProvider(lambda p, s: reply)
    kept, rejected = findings.findings_for(INV, [PAGE], provider)
    assert len(kept) == 1 and not rejected
    assert kept[0]["page"] == 18 and kept[0]["url"].endswith("#finding-1")


def test_unknown_label_is_rejected():
    reply = {"findings": [item("Flames persisted.", "the flames generally persisted", "P7")]}
    kept, rejected = findings.findings_for(INV, [PAGE], llm.FakeProvider(lambda p, s: reply))
    assert kept == [] and "unknown page label" in rejected[0]
