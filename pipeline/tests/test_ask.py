import json

import pytest

from pipeline import ask, llm


def inv(psi_id, acronym, url_id=None):
    return {"id": psi_id, "acronym": acronym, "title": acronym or psi_id, "project_type": "flight",
            "role": "prevention", "fuel_phase": "solid", "hardware": None, "related": [], "doi": None,
            "url": f"https://psi.nasa.gov/physci/repo/data/investigations/{url_id or psi_id}"}


def row(psi_id, label, phase, o2, p, outcome="unknown", diluent="N2", note=None, flow=None):
    return {"id": f"{psi_id}:{label}", "investigation_id": psi_id, "test_label": label, "fuel_material": "x",
            "fuel_phase": phase, "o2_pct": o2, "pressure_kpa": p, "flow_cm_s": flow, "diluent": diluent,
            "outcome": outcome, "note": note, "source": {"file": f"{psi_id}_Experimental table_T.csv", "row": int(label)},
            "sample": False, "extra": None}


INVESTIGATIONS = [inv("PSI-25", "BASS-II"), inv("PSI-26", "BASS"), inv("PSI-69", "FLEX"), inv("PSI-68", "FLEX-2"),
                  inv("PSI-102", "SAME"), inv("PSI-22", "ACME E-FIELD Flames"), inv("PSI-159", "ACME CFI-G")]
TESTS = [
    row("PSI-25", "1", "solid", 21.0, 101.325, note="Pressure not in the source table; ISS cabin pressure (1 atm) assumed."),
    row("PSI-25", "2", "solid", 17.4, 101.325, note="Pressure not in the source table; ISS cabin pressure (1 atm) assumed."),
    row("PSI-69", "3", "liquid", 30.0, 71.2, outcome="extinguished", diluent="N2+CO2"),
    row("PSI-69", "4", "liquid", 21.0, 101.3, outcome="sustained"),
    row("PSI-159", "5", "gas", 35.0, 50.7, outcome="extinguished"),
]
PASSAGE = {"id": "c1", "source_id": "PSI-69_Reports_FLEX Final Report.pdf", "psi_id": "PSI-69", "category": "Reports",
           "page": 12, "section": None, "part": 1, "score": 0.8,
           "text": "Introduction. Carbon dioxide was an effective suppressant for methanol droplets. "
                   "Extinction occurred at 30 % oxygen. Unrelated closing words."}
RECORD = {"id": "c2", "source_id": "PSI-22_record", "psi_id": "PSI-22", "category": "PSI record", "page": None,
          "section": "objectives", "part": 1, "score": 0.5, "text": "An electric field imitates gravity on flames."}


class FakeSearch:
    def __init__(self, results):
        self.results, self.calls = results, []

    def query(self, text, k=6, psi_ids=None):
        self.calls.append((text, psi_ids))
        return [r for r in self.results if not psi_ids or r["psi_id"] in psi_ids][:k]


@pytest.fixture
def kb():
    return ask.KnowledgeBase(INVESTIGATIONS, TESTS,
                             varied={"PSI-68": "No test-level data. Lists the planned ranges, not tests."},
                             search=FakeSearch([PASSAGE, RECORD]))


@pytest.mark.parametrize("question, expected", [
    ("Which tests burned solid fuel above 30% oxygen?", {"fuels": ["solid"], "o2": (30.0, None)}),
    ("droplets at 25 percent oxygen or more", {"fuels": ["liquid"], "o2": (25.0, None)}),
    ("oxygen below 19 %", {"o2": (None, 19.0)}),
    ("tests at 21% O2", {"o2": (20.0, 22.0)}),
    ("pressure under 10.2 psi", {"pressure": (None, pytest.approx(70.33, abs=0.01))}),
    ("anything at 2 atm or more", {"pressure": (pytest.approx(202.65), None)}),
    ("at 760 mmHg", {"pressure": (pytest.approx(96.26, abs=0.01), pytest.approx(106.39, abs=0.01))}),
    ("a 30% chance of rain", {"o2": (None, None)}),  # a percentage that is not oxygen
    ("What did FLEX find about carbon dioxide?", {"psi_ids": ["PSI-69"], "diluents": ["CO2"]}),
    ("Compare FLEX-2 and FLEX", {"psi_ids": ["PSI-68", "PSI-69"]}),
    ("BASS-II versus BASS", {"psi_ids": ["PSI-25", "PSI-26"]}),
    ("Is SAME the same as BASS?", {"psi_ids": ["PSI-102", "PSI-26"]}),  # lower-case "same" is a word
    ("tell me about psi-159", {"psi_ids": ["PSI-159"]}),
])
def test_read_question(kb, question, expected):
    q = ask.read_question(question, kb)
    for key, value in expected.items():
        got = getattr(q, key)
        assert (sorted(got) if isinstance(got, list) else got) == (sorted(value) if isinstance(value, list) else value), key


def test_habitat_words_set_ranges_unless_a_limit_is_given(kb):
    q = ask.read_question("Has any material burned at lunar-habitat conditions (30% oxygen, 10.2 psi)?", kb)
    assert q.habitat == "lunar" and q.o2 == (30.0, 100.0) and q.pressure == (29.65, 70.33)
    q = ask.read_question("lunar rover with oxygen above 40%", kb)
    assert q.o2 == (40.0, None) and q.pressure == (29.65, 70.33)


@pytest.mark.parametrize("question", [
    "What happens to flames in partial gravity on the Moon?", "How do fires behave in Mars gravity?",
    "flame spread at 1/6 g", "reduced-gravity combustion",
])
def test_partial_gravity_is_detected(kb, question):
    assert ask.read_question(question, kb).partial_gravity


def test_table_answer_summarises_matching_rows(kb):
    text, cites = ask.table_answer(ask.read_question("What oxygen levels and pressures did FLEX test?", kb), kb)
    assert text == ("In the PSI test tables, 2 tests match: FLEX (2). They cover oxygen 21 to 30 %, pressure 71.2 to "
                    "101 kPa (10.3 to 14.7 psi). Outcome: the flame went out in 1 and it burned to the end of the test in 1.")
    assert cites == [{"label": "FLEX (PSI-69): test table, 2 matching rows",
                      "url": "https://psi.nasa.gov/physci/repo/data/investigations/PSI-69#tests", "kind": "table"}]


def test_table_answer_names_assumed_pressure(kb):
    text, _ = ask.table_answer(ask.read_question("BASS-II tests", kb), kb)
    assert "Pressure for BASS-II is not in the source table; cabin pressure (1 atm) is assumed." in text


def test_no_match_gives_the_nearest_real_limit(kb):
    text, cites = ask.table_answer(ask.read_question("Which tests burned solid fuel above 30% oxygen?", kb), kb)
    assert text == ("No test in the PSI test tables matches (solid fuels, oxygen 30 % or more). "
                    "The highest oxygen level tested with solid fuels is 21 % (BASS-II, test 1).")
    assert cites[0]["label"] == "BASS-II (PSI-25): test 1, table row 1"


def test_named_investigation_without_tests_gets_its_reason(kb):
    text, _ = ask.table_answer(ask.read_question("What did FLEX-2 test?", kb), kb)
    assert text.endswith("FLEX-2: No test-level data. Lists the planned ranges, not tests.")


def test_question_without_filters_has_no_table_answer(kb):
    assert ask.table_answer(ask.read_question("What is a cool flame?", kb), kb) == (None, [])


def test_best_snippet_picks_the_relevant_sentences():
    snippet = ask.best_snippet(PASSAGE["text"], "Is carbon dioxide a good suppressant?", limit=120)
    assert snippet == "Carbon dioxide was an effective suppressant for methanol droplets. Extinction occurred at 30 % oxygen."


def test_retrieval_answer_quotes_passages_with_their_source(kb):
    reply = ask.answer("What did FLEX find about carbon dioxide as a suppressant?", kb)
    assert reply["mode"] == "retrieval"
    assert reply["answer"].startswith("In the PSI test tables, 1 test matches (diluted with CO2): FLEX (1).")
    assert "From the documents: “" in reply["answer"] and "(FLEX, FLEX Final Report, p. 12)" in reply["answer"]
    assert [c["kind"] for c in reply["citations"]] == ["table", "document"]
    doc = reply["citations"][1]
    assert doc["label"] == "FLEX (PSI-69): FLEX Final Report, p. 12" and doc["page"] == 12
    assert doc["url"].endswith("/PSI-69#source-1")
    assert kb.search.calls[-1][1] == ["PSI-69"]  # the search is limited to the named experiment


def test_nothing_found_says_so(kb):
    kb.search = FakeSearch([])
    reply = ask.answer("What is the meaning of life?", kb)
    assert reply["answer"].startswith("I could not find anything about this in the PSI combustion data.")
    assert reply["citations"] == []


def test_partial_gravity_gets_the_no_data_answer_without_ai(kb):
    provider = llm.FakeProvider(lambda p, s: pytest.fail("the AI must not be asked"))
    reply = ask.answer("What happens to flames in partial gravity on the Moon?", kb, provider, ask.Budget(5))
    assert reply["mode"] == "no_data"
    assert reply["answer"].startswith("PSI has no combustion tests in partial gravity")
    assert "LUCI and FM2" in reply["answer"] and "E-FIELD" in reply["answer"]
    assert reply["citations"][0]["url"] == ask.BRIEF_URL
    assert any(c["url"].endswith("/PSI-22#record") for c in reply["citations"])


def test_demo_question_is_served_from_the_file(kb):
    kb.demo = {ask.normalise("What did FLEX find?"): {"question": "What did FLEX find?", "answer": "Stored.",
                                                       "citations": [{"label": "a", "url": "u"}]}}
    reply = ask.answer("  what did FLEX find  ", kb, llm.FakeProvider(lambda p, s: pytest.fail("no AI")), ask.Budget(5))
    assert reply == {"answer": "Stored.", "citations": [{"label": "a", "url": "u"}], "mode": "demo"}


@pytest.mark.parametrize("answer, ok", [
    ("FLEX tested 30 % oxygen at 71.2 kPa.", True),
    ("That is about 10.3 psi.", True),           # 71.2 kPa converted
    ("Two experiments, 3 tests.", True),          # small counts are allowed
    ("Extinction happened at 25 % oxygen.", False),
    ("It took 1,867 tests.", False),
])
def test_numbers_must_come_from_the_sources(answer, ok):
    assert ask.numbers_supported(answer, "oxygen 30 % at 71.2 kPa")[0] is ok


def ai(reply):
    return llm.FakeProvider(lambda prompt, schema: reply)


def test_ai_answer_is_used_when_its_numbers_check_out(kb):
    provider = ai({"answer": "In FLEX, carbon dioxide put out the flame at 30 % oxygen.", "sources_used": [1, 0, 9]})
    budget = ask.Budget(5)
    reply = ask.answer("What did FLEX find about carbon dioxide as a suppressant?", kb, provider, budget)
    assert reply["mode"] == "ai" and reply["answer"].startswith("In FLEX, carbon dioxide")
    assert [c["kind"] for c in reply["citations"]] == ["table", "document"]  # source 9 does not exist
    assert budget.used == 1


def test_ai_answer_with_an_invented_number_falls_back(kb):
    provider = ai({"answer": "Carbon dioxide worked at 12 % oxygen.", "sources_used": [1]})
    reply = ask.answer("What did FLEX find about carbon dioxide as a suppressant?", kb, provider, ask.Budget(5))
    assert reply["mode"] == "retrieval" and reply["note"] == "numbers not in the sources: 12%"


def test_quota_error_switches_to_retrieval_for_the_rest_of_the_day(kb):
    class Exhausted(llm.FakeProvider):
        def complete_json(self, prompt, schema, system):
            self.calls += 1
            raise llm.QuotaError("quota used up")

    provider, budget = Exhausted(), ask.Budget(5)
    first = ask.answer("What did FLEX find about carbon dioxide as a suppressant?", kb, provider, budget)
    second = ask.answer("What oxygen levels did FLEX test?", kb, provider, budget)
    assert first["note"] == "AI quota used up" and second["note"] == "daily AI budget used"
    assert provider.calls == 1


def test_daily_budget_is_respected_and_cache_hits_are_free(kb):
    provider = ai({"answer": "FLEX tested carbon dioxide.", "sources_used": [1]})
    budget = ask.Budget(1)
    question = "What did FLEX find about carbon dioxide as a suppressant?"
    assert ask.answer(question, kb, provider, budget)["mode"] == "ai"
    assert ask.answer(question, kb, provider, budget)["mode"] == "ai"  # same prompt: answered from the cache
    assert ask.answer("What oxygen levels did FLEX test?", kb, provider, budget)["note"] == "daily AI budget used"
    assert provider.calls == 1


def test_knowledge_base_loads_the_real_output_files():
    kb = ask.KnowledgeBase.load()
    assert len(kb.investigations) == 24 and len(kb.tests) > 1000 and kb.varied
    q = ask.read_question("Which tests burned solid fuel above 30% oxygen?", kb)
    text, _ = ask.table_answer(q, kb)
    assert text.startswith("No test in the PSI test tables matches (solid fuels, oxygen 30 % or more).")


@pytest.mark.parametrize("text, expected", [
    ("Acknowledgements. We thank the ground teams at Glenn Research Center for their support.", True),
    ("References 1. Wichman, I. S.: Theory of Opposed-Flow Flame Spread.", True),
    ("Olson et al. 2009, Proc. Combust. Inst. 32; Ferkul et al. 2012, J. Fire Sci.; Urban 2013 Conference paper.", True),
    ("Flame spread rates increased with flow speed up to 10 cm/s and then levelled off in every test.", False),
])
def test_reference_lists_are_recognised(text, expected):
    assert ask.looks_like_references(text) is expected


def test_reference_passages_only_fill_gaps(kb):
    refs = {**PASSAGE, "id": "c9", "page": 99, "text": "References 1. Smith et al. 2010, Proc. Combust. Inst."}
    kb.search = FakeSearch([refs, PASSAGE])
    found = ask.passages("carbon dioxide suppressant", ask.Query(), kb)
    assert [p["page"] for p in found] == [12, 99]


def test_materials_are_listed_when_asked(kb):
    q = ask.read_question("Which solid materials were tested?", kb)
    text, _ = ask.table_answer(q, kb, wants_materials=True)
    assert "Materials most often named in the tables: x (2)." in text
    assert "Materials" not in ask.table_answer(q, kb)[0]


def test_tidy_removes_a_copied_source_label():
    assert ask.tidy("Test-table summary: In the PSI test tables, 2 tests match.") == "In the PSI test tables, 2 tests match."
    assert ask.tidy("[0] test-table summary:  Two tests.") == "Two tests."
    assert ask.tidy("FLEX tested CO2.") == "FLEX tested CO2."


def test_partial_gravity_answer_adds_the_saffire_comparison(kb):
    kb.investigations.append({**INVESTIGATIONS[0], "id": "PSI-99", "acronym": "SAFFIRE-II",
                              "url": "https://psi.nasa.gov/physci/repo/data/investigations/PSI-99"})
    kb.__post_init__()
    kb.gravity_paired = "In Saffire-II, 4 of 9 samples barely burned in microgravity."
    reply = ask.answer("What happens to flames in partial gravity on the Moon?", kb)
    assert reply["answer"].endswith("In Saffire-II, 4 of 9 samples barely burned in microgravity.")
    assert reply["citations"][2]["label"] == "SAFFIRE-II (PSI-99): test table, microgravity and normal-gravity burn results"
    kb.investigations.pop()


@pytest.mark.parametrize("raw, clean", [
    ("SAME studied smoke detection [2].", "SAME studied smoke detection."),
    ("Both FLEX [1, 3] and CFI [4 and 6] used helium.", "Both FLEX and CFI used helium."),
    ("Sources [1]-[2] agree.", "Sources agree."),
    ("Oxygen was 21 % [5].", "Oxygen was 21 %."),
])
def test_tidy_removes_source_numbers(raw, clean):
    assert ask.tidy(raw) == clean


def test_assumed_oxygen_and_pressure_are_both_named(kb):
    note = ("Oxygen not in the source table; ISS cabin air (21 % oxygen) assumed. "
            "Pressure not in the source table; ISS cabin pressure (1 atm) assumed.")
    kb.tests = TESTS + [row("PSI-102", "9", "solid", 21.0, 101.325, note=note)]
    text, _ = ask.table_answer(ask.read_question("solid materials at 21% oxygen", kb), kb)
    assert "Pressure for BASS-II is not in the source table; cabin pressure (1 atm) is assumed." in text
    assert "Oxygen and pressure for SAME are not in the source tables; ISS cabin air (21 % oxygen, 14.7 psi) is assumed." in text


FINDINGS = [
    {"investigation_id": "PSI-69", "finding": "Carbon dioxide put out methanol droplet flames sooner than nitrogen.",
     "quote": "carbon dioxide extinguished the flames sooner", "source_id": "PSI-69_Reports_FLEX Final Report.pdf",
     "page": 12, "section": None, "url": "https://psi.nasa.gov/physci/repo/data/investigations/PSI-69#finding-1"},
    {"investigation_id": "PSI-25", "finding": "Extinction flow speeds were about 1 cm/s for thin samples.",
     "quote": "extinction velocities are found to be about 1 cm/s", "source_id": "PSI-25_Reports_BASS-II Summary Report.pdf",
     "page": 31, "section": None, "url": "https://psi.nasa.gov/physci/repo/data/investigations/PSI-25#finding-1"},
]


def test_findings_of_a_named_experiment_come_first(kb):
    kb.findings = FINDINGS
    q = ask.read_question("What did BASS-II find?", kb)
    _, _, found = ask.gather("What did BASS-II find?", q, kb)
    assert found[0]["kind"] == "finding" and found[0]["psi_id"] == "PSI-25"
    assert all(f["psi_id"] == "PSI-25" for f in found if f.get("kind") == "finding")


def test_unrelated_findings_are_left_out_without_a_named_experiment(kb):
    kb.findings = FINDINGS
    q = ask.read_question("What is a cool flame?", kb)
    assert ask.key_findings("What is a cool flame?", q, kb) == []  # no embedder, no keyword overlap


def test_retrieval_answer_leads_with_findings(kb):
    kb.findings = FINDINGS
    reply = ask.answer("What did BASS-II find?", kb)
    assert reply["answer"].startswith("Extinction flow speeds were about 1 cm/s for thin samples (BASS-II, ")
    assert reply["citations"][0]["kind"] == "table" and any(c["kind"] == "finding" for c in reply["citations"])
    finding = next(c for c in reply["citations"] if c["kind"] == "finding")
    assert finding["snippet"] == "extinction velocities are found to be about 1 cm/s" and finding["page"] == 31


def test_findings_are_labelled_for_the_ai(kb):
    kb.findings = FINDINGS
    seen = {}

    def responder(prompt, schema):
        seen["prompt"] = prompt
        return {"answer": "BASS-II found extinction flow speeds of about 1 cm/s.", "sources_used": [0, 1]}

    reply = ask.answer("What did BASS-II find?", kb, llm.FakeProvider(responder), ask.Budget(5))
    assert "Key finding of BASS-II (PSI-25)" in seen["prompt"] and "Quote: “extinction velocities" in seen["prompt"]
    assert reply["mode"] == "ai"


@pytest.mark.parametrize("answer_text, ok", [
    ("CFI-G and Flame Design ran gas flames above 2 atm.", True),
    ("The Cool Flames Investigation with Gases ran them.", True),
    ("No, the experiments did not run gas flames above 2 atm.", False),
])
def test_an_answer_must_name_the_tests_the_table_lists(kb, answer_text, ok):
    kb.investigations.append({**INVESTIGATIONS[0], "id": "PSI-10", "acronym": "ACME/Flame Design",
                              "title": "Advanced Combustion via Microgravity Experiments / Flame Design"})
    kb.investigations[-2]["title"] = "Advanced Combustion via Microgravity Experiments / Cool Flames Investigation with Gases"
    kb.__post_init__()
    table = "In the PSI test tables, 30 tests match (gas flames, pressure 203 kPa (29.4 psi) or more): ACME CFI-G (20), ACME Flame Design (10). They cover oxygen 20 to 40 %."
    assert (ask.contradicts_table(answer_text, table, kb) is None) is ok
    assert ask.contradicts_table("No tests.", "No test in the PSI test tables matches (solid fuels).", kb) is None
    kb.investigations.pop()
