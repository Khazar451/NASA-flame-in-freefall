import argparse
import json

import pytest

from pipeline import gravity, validate
from pipeline.models import GravityFile


def inv(psi_id, acronym, kind="flight"):
    return {"id": psi_id, "acronym": acronym, "title": acronym or psi_id, "project_type": kind, "role": "prevention",
            "fuel_phase": "solid", "hardware": None, "related": [], "doi": None,
            "url": f"https://psi.nasa.gov/physci/repo/data/investigations/{psi_id}"}


def saffire(sample, material, ug_length, ug_rate, g_length, g_rate, row):
    return {"id": f"PSI-99:{sample}", "investigation_id": "PSI-99", "test_label": sample, "fuel_material": material,
            "fuel_phase": "solid", "o2_pct": 22.1, "pressure_kpa": 101.325, "flow_cm_s": 20.0, "diluent": None,
            "outcome": "unknown", "note": None, "sample": False,
            "source": {"file": "PSI-99_Experimental table_SAFFIRE-2.csv", "row": row},
            "extra": {k: v for k, v in {"flow_direction": "Concurrent", "microgravity_burn_length": ug_length,
                                         "microgravity_spread_rate": ug_rate, "normal_gravity_burn_length": g_length,
                                         "normal_gravity_spread_rate": g_rate}.items() if v is not None}}


INVESTIGATIONS = [inv("PSI-22", "ACME E-FIELD Flames"), inv("PSI-99", "SAFFIRE-II"), inv("PSI-60", None, "ground")]
TESTS = [
    saffire("2-1", "Silicone", "~ 0", None, "Complete", "Acceleratory", 1),
    saffire("2-2", "Silicone", "~ 0", None, "~ 0", None, 2),
    saffire("2-5", "SIBAL", "29 cm", "2.1 mm/s", "Complete", "Acceleratory", 3),
    saffire("2-7", "PMMA & Nomex", "~ 0 (Nomex)", None, "~ 0 (Nomex)", None, 4),
]
RECORDS = {
    "PSI-22": {"accession": "PSI-22", "objective": "Study flames.",
               "approach": "This unique environment is ideal for isolating electric field effects from buoyancy forces. "
                           "The field drives a flow, driving ion-driving convection in the flame zone."},
    "PSI-99": {"accession": "PSI-99", "objective": "Determine how rapidly a large scale fire grows in low-gravity conditions.",
               "hypothesis": "Fires differ in normal and reduced gravity (Olson et al. 2009, doi.org/10.1/x).",
               "researchImpacts": "Microgravity is interesting."},
    "PSI-60": {"accession": "PSI-60", "objective": "Reconcile microgravity and normal gravity smoke-point behaviour of jets."},
}
CHUNKS = [{"psi_id": "PSI-22", "source_id": "PSI-22_Presentations_Chien et al 2019.pdf", "page": 6,
           "text": "drop tower Karnani, S. et al., Simulating Gravity in Microgravity Combustion Using Electric Fields, "
                   "CST, 2012 -0.6 0 kV/cm"}]
TABLE = ("Sample Number,Material\n2-1,Silicone\nNote 1 Derived from measured CO2 production.,\n"
         "Note 2 The PMMA portion was completely consumed but the Nomex was not ignited.,\n")


@pytest.fixture
def raw(tmp_path, monkeypatch):
    folder = tmp_path / "raw" / "PSI-99" / "tables"
    folder.mkdir(parents=True)
    (folder / "PSI-99_Experimental table_SAFFIRE-2.csv").write_text(TABLE)
    monkeypatch.setattr(gravity.transform_tests.config, "RAW_DIR", tmp_path / "raw")
    return tmp_path


def built(raw):
    return gravity.build(INVESTIGATIONS, TESTS, RECORDS, CHUNKS)


def test_levels_say_where_data_exists(raw):
    levels = {level["id"]: level for level in built(raw)["levels"]}
    assert levels["microgravity"]["n_tests"] == 4 and levels["microgravity"]["investigations"] == ["PSI-22", "PSI-99"]
    assert levels["moon"]["status"] == levels["mars"]["status"] == "no_data"
    assert levels["moon"]["n_tests"] == levels["mars"]["n_tests"] == 0
    assert levels["earth"]["status"] == "comparison_only" and "4 samples" in levels["earth"]["note"]


def test_stand_in_quotes_are_word_for_word(raw):
    evidence = built(raw)["stand_in"]["evidence"]
    assert [q["text"] for q in evidence] == [
        "This unique environment is ideal for isolating electric field effects from buoyancy forces.",
        "The field drives a flow, driving ion-driving convection in the flame zone.",
        "Karnani, S. et al., Simulating Gravity in Microgravity Combustion Using Electric Fields, CST, 2012",
    ]
    assert evidence[2]["source_label"] == "PSI-22_Presentations_Chien et al 2019, p. 6"


def test_statements_compare_gravity_levels_and_skip_references(raw):
    statements = built(raw)["statements"]
    assert [(s["investigation_id"], s["source_label"]) for s in statements] == [
        ("PSI-22", "PSI record, approach"),
        ("PSI-60", "PSI record, objectives"),
        ("PSI-99", "PSI record, objectives"),
    ]
    assert all("doi.org" not in s["text"] for s in statements)
    assert "Microgravity is interesting." not in [s["text"] for s in statements]


def test_paired_results_and_summary(raw):
    data = built(raw)
    assert [r["sample"] for r in data["paired_results"]] == ["2-1", "2-2", "2-5", "2-7"]
    first = data["paired_results"][0]
    assert (first["microgravity_burn_length"], first["normal_gravity_burn_length"]) == ("~ 0", "Complete")
    assert first["source"]["row"] == 1
    assert data["paired_summary"] == (
        "In Saffire-II, 3 of 4 samples (PMMA & Nomex, Silicone) barely burned in microgravity (burn length about 0), "
        "but 1 of those 3 burned at normal gravity (Complete). The table does not say how the normal-gravity tests were run."
    )
    assert data["paired_notes"] == [
        "Note 1 Derived from measured CO2 production.",
        "Note 2 The PMMA portion was completely consumed but the Nomex was not ignited.",
    ]


def test_upcoming_experiments_cite_the_brief(raw):
    upcoming = built(raw)["upcoming"]
    assert [u["name"] for u in upcoming] == ["LUCI", "FM2"]
    assert all(u["url"] == gravity.BRIEF_URL and "Not in PSI yet" in u["note"] for u in upcoming)


def test_validate_checks_gravity_json(raw):
    out = raw / "out"
    out.mkdir()
    (out / "investigations.json").write_text(json.dumps({"investigations": INVESTIGATIONS}))
    (out / "tests.json").write_text(json.dumps({"tests": TESTS}))
    (out / "gravity.json").write_text(json.dumps(built(raw)))
    assert validate.check(out)[0] == []
    (out / "tests.json").write_text(json.dumps({"tests": TESTS[:2]}))
    errors, _ = validate.check(out)
    assert any("gravity.json counts 4 microgravity tests, tests.json has 2" in e for e in errors)


def test_run_needs_its_inputs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gravity.config, "OUT_DIR", tmp_path)
    assert gravity.run(argparse.Namespace()) == 1
    assert "run the earlier steps first" in capsys.readouterr().out


def test_real_gravity_file_is_valid_and_quotes_its_sources():
    data = GravityFile.model_validate(json.loads(gravity.OUT.read_text(encoding="utf-8")))
    assert {u.name for u in data.upcoming} == {"LUCI", "FM2"}
    assert data.stand_in.investigation_id == "PSI-22" and len(data.paired_results) == 9
