import argparse
import copy
import json

import pytest
from pydantic import ValidationError

from pipeline import models, validate

# Shaped exactly like the rows in docs/data-contract.md, with no pipeline extras.
INVESTIGATION = {
    "id": "PSI-25",
    "acronym": "BASS-II",
    "title": "Burning and Suppression of Solids - II",
    "project_type": "flight",
    "role": "prevention",
    "fuel_phase": "solid",
    "hardware": "MSG flow duct",
    "related": ["PSI-26"],
    "doi": "10.60555/4qc4-de67",
    "url": "https://psi.nasa.gov/physci/repo/data/investigations/PSI-25",
}
OTHER = {**INVESTIGATION, "id": "PSI-26", "acronym": "BASS", "related": ["PSI-25"], "doi": None}
TEST_ROW = {
    "id": "PSI-25:B1",
    "investigation_id": "PSI-25",
    "test_label": "B1",
    "fuel_material": "100 micron PMMA film 2 cm wide",
    "fuel_phase": "solid",
    "o2_pct": 22.2,
    "pressure_kpa": 101.325,
    "flow_cm_s": None,
    "diluent": "N2",
    "outcome": "unknown",
    "note": "Pressure not in the source table; ISS cabin pressure assumed.",
    "source": {"file": "PSI-25_Experimental table_BASS-II.csv", "row": 1},
    "sample": False,
}


def write(folder, investigations=None, tests=None):
    investigations = [INVESTIGATION, OTHER] if investigations is None else investigations
    tests = [TEST_ROW] if tests is None else tests
    (folder / "investigations.json").write_text(
        json.dumps({"meta": {"roles_reviewed": False}, "investigations": investigations})
    , encoding="utf-8")
    (folder / "tests.json").write_text(json.dumps({"meta": {}, "tests": tests}), encoding="utf-8")
    return folder


def row(**changes):
    return {**copy.deepcopy(TEST_ROW), **changes}


def test_contract_rows_validate_without_extras():
    inv = models.Investigation.model_validate(INVESTIGATION)
    assert inv.objectives is None and inv.hardware_family is None
    assert models.TestRow.model_validate(TEST_ROW).extra is None


def test_assumed_lists_only_numeric_fields():
    assert models.TestRow.model_validate(TEST_ROW).assumed == []
    assert models.TestRow.model_validate(row(assumed=["o2_pct", "pressure_kpa"])).assumed == ["o2_pct", "pressure_kpa"]
    with pytest.raises(Exception):
        models.TestRow.model_validate(row(assumed=["outcome"]))


def test_extras_are_accepted():
    models.Investigation.model_validate({**INVESTIGATION, "n_publications": 17, "sub_area": "Spacecraft Fire Safety"})
    models.TestRow.model_validate(row(extra={"burning_rate_mm2_s": 0.52, "flow_direction": "opposed"}))


@pytest.mark.parametrize(
    "model, data",
    [
        (models.Investigation, {**INVESTIGATION, "role": "safety"}),
        (models.Investigation, {**INVESTIGATION, "id": "BASS-II"}),
        (models.Investigation, {**INVESTIGATION, "fuel_fase": "solid"}),  # misspelt field
        (models.Investigation, {k: v for k, v in INVESTIGATION.items() if k != "doi"}),
        (models.TestRow, row(outcome="burned")),
        (models.TestRow, row(o2_pct="high")),
        (models.TestRow, row(source={"file": "x.csv", "row": 0})),
        (models.TestRow, {k: v for k, v in TEST_ROW.items() if k != "sample"}),
    ],
)
def test_rows_that_break_the_contract_are_rejected(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_schema_json_is_up_to_date():
    assert models.SCHEMA_JSON.read_text(encoding="utf-8") == models.schema_text(), (
        "data/schema.json is stale; run: python -m pipeline.models"
    )


def test_valid_folder_passes(tmp_path):
    errors, warnings = validate.check(write(tmp_path))
    assert errors == []
    assert warnings == ["1 investigation(s) have no test rows: PSI-26"]


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"o2_pct": 121.0}, "o2_pct 121.0 is outside 0 to 100"),
        ({"o2_pct": -1.0}, "o2_pct -1.0 is outside 0 to 100"),
        ({"pressure_kpa": 0.0}, "pressure_kpa 0.0 is outside"),
        ({"pressure_kpa": 14700.0}, "pressure_kpa 14700.0 is outside"),
        ({"flow_cm_s": -5.0}, "flow_cm_s -5.0 is outside"),
        ({"investigation_id": "PSI-999"}, "investigation_id PSI-999 is not in investigations.json"),
        ({"source": None}, "must record its source file and row"),
        ({"o2_pct": None, "assumed": ["o2_pct"]}, "o2_pct is listed as assumed but has no value"),
    ],
)
def test_bad_test_rows_are_errors(tmp_path, changes, expected):
    errors, _ = validate.check(write(tmp_path, tests=[row(**changes)]))
    assert len(errors) == 1 and expected in errors[0]


def test_nulls_and_boundary_values_are_allowed(tmp_path):
    rows = [
        row(id="a", o2_pct=None, pressure_kpa=None, flow_cm_s=None),
        row(id="b", o2_pct=0.0, flow_cm_s=0.0),
        row(id="c", o2_pct=100.0),
        row(id="d", source=None, sample=True),
    ]
    errors, warnings = validate.check(write(tmp_path, tests=rows))
    assert errors == []
    assert "1 of 4 test rows are placeholders (sample=true)" in warnings


def test_duplicate_ids_are_errors(tmp_path):
    errors, _ = validate.check(write(tmp_path, investigations=[INVESTIGATION, INVESTIGATION], tests=[TEST_ROW, TEST_ROW]))
    assert "investigations.json: id PSI-25 appears more than once" in errors
    assert "tests.json: id PSI-25:B1 appears more than once" in errors


def test_doi_with_prefix_is_an_error(tmp_path):
    bad = {**INVESTIGATION, "doi": "https://doi.org/10.60555/4qc4-de67"}
    errors, _ = validate.check(write(tmp_path, investigations=[bad, OTHER]))
    assert len(errors) == 1 and "bare identifier" in errors[0]


def test_schema_errors_name_the_row_and_field(tmp_path):
    errors, _ = validate.check(write(tmp_path, tests=[row(outcome="burned")]))
    assert len(errors) == 1 and errors[0].startswith("tests.json: tests.0.outcome:")


def test_missing_or_broken_files_are_errors(tmp_path):
    errors, _ = validate.check(tmp_path)
    assert len(errors) == 2 and all("file not found" in e for e in errors)
    write(tmp_path)
    (tmp_path / "tests.json").unlink()
    errors, warnings = validate.check(tmp_path)
    assert errors == [] and warnings == ["tests.json is not built yet; only investigations.json was checked"]
    write(tmp_path)
    (tmp_path / "tests.json").write_text("{not json", encoding="utf-8")
    errors, _ = validate.check(tmp_path)
    assert len(errors) == 1 and "not valid JSON" in errors[0]


def test_run_exit_codes(tmp_path, monkeypatch, capsys):
    assert validate.run(argparse.Namespace(folder=write(tmp_path))) == 0
    write(tmp_path, tests=[row(o2_pct=500.0)])
    assert validate.run(argparse.Namespace(folder=tmp_path)) == 1
    assert "FAILED with 1 error(s)" in capsys.readouterr().out

    empty = tmp_path / "out"
    empty.mkdir()
    monkeypatch.setattr(validate.config, "OUT_DIR", empty)
    assert validate.run(argparse.Namespace()) == 0  # nothing built yet is not a failure
    write(empty, tests=[row(o2_pct=500.0)])
    assert validate.run(argparse.Namespace()) == 1
