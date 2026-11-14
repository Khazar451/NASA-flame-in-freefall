import argparse
import json

import pytest

from pipeline import coverage, transform_tests, validate, varied


def row(psi_id, label, phase="liquid", o2=21.0, p=101.3, flow=None, outcome="unknown", source="T.csv", **extra):
    return {"id": f"{psi_id}:{label}", "investigation_id": psi_id, "test_label": label, "fuel_material": "Heptane",
            "fuel_phase": phase, "o2_pct": o2, "pressure_kpa": p, "flow_cm_s": flow, "diluent": "N2",
            "outcome": outcome, "note": None, "source": {"file": f"{psi_id}_Experimental table_{source}", "row": 1},
            "sample": False, "extra": extra or None}


ASSUMED = [("PSI-25", "BASS-II.csv", {"pressure_kpa"})]


@pytest.mark.parametrize("value, bands, expected", [
    (18.99, coverage.O2_BANDS, "o2_below_air"), (19.0, coverage.O2_BANDS, "o2_air"),
    (30.0, coverage.O2_BANDS, "o2_lunar"), (100.0, coverage.O2_BANDS, "o2_high"),
    (29.65, coverage.PRESSURE_BANDS, "p_lunar"), (70.33, coverage.PRESSURE_BANDS, "p_reduced"),
    (70.32, coverage.PRESSURE_BANDS, "p_lunar"), (101.325, coverage.PRESSURE_BANDS, "p_cabin"),
    (900.0, coverage.PRESSURE_BANDS, "p_elevated"),
])
def test_band_edges(value, bands, expected):
    assert coverage.band_of(value, bands) == expected


def test_lunar_cells_are_exactly_the_lunar_range():
    data = coverage.build([], [])
    lunar = {(c["o2_band"], c["pressure_band"]) for c in data["cells"] if "lunar" in c["habitats"]}
    assert lunar == {("o2_lunar", "p_lunar"), ("o2_high", "p_lunar")}
    cabin = {(c["o2_band"], c["pressure_band"]) for c in data["cells"] if "cabin" in c["habitats"]}
    assert cabin == {("o2_air", "p_cabin")}
    assert len(data["cells"]) == 75 and data["meta"]["empty_cells"] == 75


def test_tests_are_counted_once_either_on_or_off_the_grid():
    tests = [
        row("PSI-69", "1", o2=34.0, p=50.0, outcome="extinguished"),
        row("PSI-69", "2", o2=34.0, p=60.0, outcome="sustained"),
        row("PSI-25", "B1", phase="solid", o2=21.0, p=101.325, source="BASS-II.csv"),
        row("PSI-159", "I1", phase="gas", o2=None, p=101.3, oxidizer_o2_pct=85.0),
        row("PSI-107", "S1", phase="gas", o2=None, p=None, flow=12.0),
        row("PSI-69", "3", o2=21.0, p=None),
    ]
    data = coverage.build(tests, ASSUMED)
    cell = next(c for c in data["cells"] if (c["fuel_phase"], c["o2_band"], c["pressure_band"]) == ("liquid", "o2_lunar", "p_lunar"))
    assert cell["n_tests"] == 2 and cell["outcomes"] == {"extinguished": 1, "sustained": 1}
    assert cell["investigations"] == ["PSI-69"] and cell["habitats"] == ["lunar"]
    solid = next(c for c in data["cells"] if c["fuel_phase"] == "solid" and c["n_tests"])
    assert (solid["o2_band"], solid["pressure_band"], solid["n_pressure_assumed"]) == ("o2_air", "p_cabin", 1)
    reasons = {o["reason"]: o["n_tests"] for o in data["not_on_grid"]}
    assert reasons == {
        "inverse flame: oxygen comes from the burner, no ambient oxygen level": 1,
        "source table gives neither oxygen nor pressure": 1,
        "source table gives no pressure": 1,
    }
    assert data["meta"]["tests_on_grid"] + data["meta"]["tests_not_on_grid"] == len(tests)


def test_assumed_in_matches_by_investigation_and_table():
    table = [("PSI-25", "BASS-II.csv", {"pressure_kpa"}), ("PSI-98", "SAFFIRE-1.csv", {"pressure_kpa"})]
    assert transform_tests.assumed_in(row("PSI-25", "B1", source="BASS-II.csv"), table) == {"pressure_kpa"}
    assert transform_tests.assumed_in(row("PSI-69", "1", source="BASS-II.csv"), table) == set()
    assert transform_tests.assumed_in(row("PSI-25", "B1", source="Other.csv"), table) == set()


def test_real_mappings_assume_cabin_air_only_where_agreed():
    found = {(psi_id, tuple(sorted(fields))) for psi_id, _, fields in transform_tests.assumed_fields()}
    assert found == {
        ("PSI-25", ("pressure_kpa",)), ("PSI-98", ("pressure_kpa",)), ("PSI-99", ("pressure_kpa",)),
        ("PSI-101", ("o2_pct", "pressure_kpa")), ("PSI-102", ("o2_pct", "pressure_kpa")),
    }


def test_measurement_noise_is_not_variation_and_assumptions_are_named():
    rows = [row("PSI-25", f"B{i}", phase="solid", o2=o2, p=101.325, source="BASS-II.csv",
                flow_direction="Opposed" if i % 2 else "opposed ")
            for i, o2 in enumerate([21.0, 21.2, 20.8, 17.4], start=1)]
    rows[3]["fuel_material"] = "PMMA rod"
    data = varied.build(rows, ["PSI-25"], ASSUMED, {})
    entry = data["investigations"][0]
    by_field = {c["field"]: c for c in entry["conditions"]}
    assert by_field["o2_pct"]["status"] == "varied" and by_field["o2_pct"]["levels"] == [17, 21]
    assert by_field["pressure_kpa"]["status"] == "constant" and by_field["pressure_kpa"]["assumed"] is True
    assert by_field["flow_cm_s"]["status"] == "not_recorded"
    assert by_field["extra.flow_direction"]["status"] == "constant"  # case and spacing ignored
    assert by_field["extra.flow_direction"]["levels"] == ["Opposed"]  # but shown as written
    assert "extra.nozzle_mm" not in by_field  # extras only appear where reported
    assert entry["summary"] == (
        "Varied: fuel (2 different entries); oxygen 17.4–21.2 % (2 levels). Constant: diluent gas N2; "
        "flow direction Opposed. Assumed, not in the table: pressure 101 kPa. Not recorded: flow speed."
    )


def test_measured_constant_is_not_called_assumed():
    rows = [row("PSI-69", str(i), p=101.3) for i in range(3)]
    entry = varied.build(rows, ["PSI-69"], ASSUMED, {})["investigations"][0]
    pressure = next(c for c in entry["conditions"] if c["field"] == "pressure_kpa")
    assert pressure["status"] == "constant" and pressure["assumed"] is False
    assert "Constant: fuel Heptane; oxygen 21 %; pressure 101 kPa; diluent gas N2." in entry["summary"]


def test_investigation_without_tests_gives_the_reason():
    data = varied.build([], ["PSI-68", "PSI-60"], [], {"PSI-68": "Lists planned ranges, not tests."})
    assert [e["summary"] for e in data["investigations"]] == [
        "No test-level data. Lists planned ranges, not tests.",
        "No test-level data. PSI has no experimental table for this investigation.",
    ]


def test_partly_recorded_values_are_reported():
    rows = [row("PSI-107", "1", phase="gas", o2=None, p=None, flow=10.0), row("PSI-107", "2", phase="gas", o2=None, p=None)]
    entry = varied.build(rows, ["PSI-107"], [], {})["investigations"][0]
    assert entry["summary"].endswith("Recorded for some tests only: flow speed (1 of 2).")


def test_validate_catches_stale_phase6_files(tmp_path):
    inv = {"id": "PSI-69", "acronym": None, "title": "t", "project_type": "flight", "role": "suppression",
           "fuel_phase": "liquid", "hardware": None, "related": [], "doi": None, "url": "https://x"}
    tests = [row("PSI-69", "1"), row("PSI-69", "2")]
    (tmp_path / "investigations.json").write_text(json.dumps({"investigations": [inv]}), encoding="utf-8")
    (tmp_path / "tests.json").write_text(json.dumps({"tests": tests}), encoding="utf-8")
    (tmp_path / "coverage.json").write_text(json.dumps(coverage.build(tests, [])), encoding="utf-8")
    (tmp_path / "varied.json").write_text(json.dumps(varied.build(tests, ["PSI-69"], [], {})), encoding="utf-8")
    assert validate.check(tmp_path)[0] == []

    (tmp_path / "tests.json").write_text(json.dumps({"tests": tests[:1]}), encoding="utf-8")  # tests changed, nothing rebuilt
    errors, _ = validate.check(tmp_path)
    assert any("coverage.json counts 2 tests but tests.json has 1" in e for e in errors)
    assert any("varied.json: PSI-69 counts 2 tests, tests.json has 1" in e for e in errors)


def test_steps_need_their_inputs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(coverage.config, "OUT_DIR", tmp_path)
    assert coverage.run(argparse.Namespace()) == 1
    assert varied.run(argparse.Namespace()) == 1
    assert "run transform-tests first" in capsys.readouterr().out
