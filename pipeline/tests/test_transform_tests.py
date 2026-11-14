import argparse
import json

import pytest
import yaml

from pipeline import transform_tests as tt
from pipeline import validate

FLEX_CSV = (
    "﻿Test #,Fuel,Ambient  pressure; mmHg,O; mole fraction,N; mole fraction,CO; mole fraction,"
    "Burn time; s,Test end\r\n"
    "1,Methanol,760,0.21,0.79,0,12.5,Extinction\r\n"
    "2,Heptane,380,0.18,0.52,0.30,~9,Completion\r\n"
    "2,Heptane,0,0.18,0.82,0,–,Disruption\r\n"
    ",,,,,,,\r\n"
)
FLEX_MAPPING = {
    "investigation": "PSI-69",
    "table": "FLEX.csv",
    "fuel_phase": "liquid",
    "null_values": ["–"],
    "label": {"column": "Test #"},
    "fuel_material": {"column": "Fuel"},
    "o2_pct": {"column": "O; mole fraction", "scale": 100},
    "pressure_kpa": {"column": "Ambient pressure; mmHg", "scale": 0.1333224},
    "flow_cm_s": None,
    "diluent": {"components": {"N2": "N; mole fraction", "CO2": "CO; mole fraction"}},
    "outcome": {"column": "Test end", "map": {"Extinction": "extinguished", "Completion": "sustained",
                                               "Disruption": "unknown"}},
    "extra": {"burn_time_s": {"column": "Burn time; s", "parse": "first_number"},
              "test_end": {"column": "Test end", "type": "text"}},
}
SAFFIRE_CSV = (
    "Sample Number,Material,Air Flow (cm/s),Percent O2\n"
    "S1,SIBAL Fabric,20 cm/s,21.5 - 21.7\n"
    "S2,SIBAL Fabric,20 cm/s,~ 21.5\n"
    "F1,Pre-test flow visualisation,,\n"
    "Note 1 Derived from measurements,,,\n"
)
SAFFIRE_MAPPING = {
    "investigation": "PSI-98",
    "table": "SAFFIRE-1.csv",
    "fuel_phase": "solid",
    "keep_if_not_blank": ["Air Flow (cm/s)"],
    "label": {"column": "Sample Number"},
    "fuel_material": {"column": "Material"},
    "o2_pct": {"column": "Percent O2", "parse": "range_mean"},
    "pressure_kpa": {"value": 101.325, "assumed": "Pressure assumed (1 atm)."},
    "flow_cm_s": {"column": "Air Flow (cm/s)", "parse": "first_number"},
    "note": "Large-scale test.",
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    raw, mappings = tmp_path / "raw", tmp_path / "mappings"
    mappings.mkdir()
    monkeypatch.setattr(tt.config, "RAW_DIR", raw)
    monkeypatch.setattr(tt.config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tt, "MAPPINGS_DIR", mappings)
    monkeypatch.setattr(tt, "OUT", tmp_path / "out" / "tests.json")

    def add(mapping, csv_text, encoding="utf-8"):
        folder = raw / mapping["investigation"] / "tables"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{mapping['investigation']}_Experimental table_{mapping['table']}"
        (folder / name).write_bytes(csv_text.encode(encoding))
        (mappings / f"{mapping['table']}.yaml").write_text(yaml.safe_dump(mapping, allow_unicode=True))
        return folder / name

    return tmp_path, add


def rows_of(mapping, path):
    return tt.apply_mapping(mapping, path)


def test_columns_are_converted_and_every_row_keeps_its_source(workspace):
    _, add = workspace
    rows, report = rows_of(FLEX_MAPPING, add(FLEX_MAPPING, FLEX_CSV))
    assert rows[0] == {
        "id": "PSI-69:1", "investigation_id": "PSI-69", "test_label": "1",
        "fuel_material": "Methanol", "fuel_phase": "liquid",
        "o2_pct": 21.0, "pressure_kpa": 101.325024, "flow_cm_s": None,
        "diluent": "N2", "outcome": "extinguished", "note": None,
        "source": {"file": "PSI-69_Experimental table_FLEX.csv", "row": 1}, "sample": False,
        "extra": {"burn_time_s": 12.5, "test_end": "Extinction"},
    }
    assert rows[1]["diluent"] == "N2+CO2" and rows[1]["outcome"] == "sustained"
    assert rows[1]["extra"]["burn_time_s"] == 9.0  # "~9" read with first_number
    assert report["source_rows"] == 4 and report["rows"] == 3 and report["skipped_rows"] == 1
    assert report["unreadable"] == {}


def test_impossible_source_value_is_left_empty_and_explained(workspace):
    _, add = workspace
    rows, report = rows_of(FLEX_MAPPING, add(FLEX_MAPPING, FLEX_CSV))
    assert rows[2]["pressure_kpa"] is None and rows[2]["o2_pct"] == 18.0
    assert rows[2]["note"] == "The source table gives pressure as 0, which is not possible; left empty."
    assert rows[2]["source"]["row"] == 3
    assert "burn_time_s" not in rows[2]["extra"]  # the dash is a listed null value
    assert report["impossible_values_left_empty"] == {"pressure_kpa": 1}


def test_ranges_units_in_cells_assumed_values_and_skipped_rows(workspace):
    _, add = workspace
    rows, report = rows_of(SAFFIRE_MAPPING, add(SAFFIRE_MAPPING, SAFFIRE_CSV))
    assert [(r["test_label"], r["o2_pct"], r["flow_cm_s"], r["pressure_kpa"]) for r in rows] == [
        ("S1", 21.6, 20.0, 101.325), ("S2", 21.5, 20.0, 101.325),
    ]
    assert rows[0]["note"] == "Large-scale test. Pressure assumed (1 atm)."
    assert rows[0]["outcome"] == "unknown" and rows[0]["diluent"] is None and rows[0]["extra"] is None
    assert report["skipped_rows"] == 2


def test_cells_the_mapping_cannot_read_are_reported_not_hidden(workspace, capsys):
    _, add = workspace
    csv_text = FLEX_CSV.replace("760", "one atmosphere").replace("Completion", "Burned out")
    add(FLEX_MAPPING, csv_text)
    assert tt.run(argparse.Namespace()) == 1  # the build fails until the mapping covers them
    out = capsys.readouterr().out
    assert "could not read pressure_kpa: 'one atmosphere'x1" in out
    assert "could not read outcome: 'Burned out'x1" in out


def test_repeated_labels_get_a_suffix_and_the_file_validates(workspace, capsys):
    tmp_path, add = workspace
    add(FLEX_MAPPING, FLEX_CSV)
    add(SAFFIRE_MAPPING, SAFFIRE_CSV)
    (tmp_path / "mappings" / "_skipped.yaml").write_text(
        yaml.safe_dump([{"investigation": "PSI-68", "file": "Flex2.csv", "reason": "ranges, not tests"}])
    )
    assert tt.run(argparse.Namespace()) == 0
    data = json.loads((tmp_path / "out" / "tests.json").read_text())
    assert [t["id"] for t in data["tests"]] == ["PSI-69:1", "PSI-69:2", "PSI-69:2~2", "PSI-98:S1", "PSI-98:S2"]
    assert data["meta"]["total_rows"] == 5 and data["meta"]["rows_with_o2_and_pressure"] == 4
    assert data["meta"]["tables_not_used"][0]["investigation"] == "PSI-68"
    assert [t["file"] for t in data["meta"]["tables"]] == [
        "PSI-69_Experimental table_FLEX.csv", "PSI-98_Experimental table_SAFFIRE-1.csv",
    ]
    assert "1 repeated labels renamed, 0 unreadable cells" in capsys.readouterr().out

    investigations = [
        {"id": psi_id, "acronym": None, "title": "t", "project_type": "flight", "role": "prevention",
         "fuel_phase": "solid", "hardware": None, "related": [], "doi": None, "url": "https://x"}
        for psi_id in ("PSI-69", "PSI-98")
    ]
    (tmp_path / "out" / "investigations.json").write_text(json.dumps({"investigations": investigations}))
    errors, _ = validate.check(tmp_path / "out")
    assert errors == []


def test_table_that_is_not_utf8_and_template_labels(workspace):
    _, add = workspace
    mapping = {**SAFFIRE_MAPPING, "table": "Spice.csv", "investigation": "PSI-107",
               "label": {"template": "day {Sample Number} row {row}"}, "null_values": ["–"],
               "o2_pct": None, "pressure_kpa": None, "keep_if_not_blank": [],
               "skip_if_startswith": {"Material": ["pre-test"], "Sample Number": ["Note"]},
               "flow_cm_s": {"column": "Air Flow (cm/s)", "parse": "first_number"}}
    text = SAFFIRE_CSV.replace("S2,SIBAL Fabric,20 cm/s", "S2,SIBAL Fabric,–")
    rows, report = rows_of(mapping, add(mapping, text, encoding="cp1252"))
    assert [r["test_label"] for r in rows] == ["day S1 row 1", "day S2 row 2"]
    assert rows[1]["flow_cm_s"] is None and report["unreadable"] == {}


def test_mapping_that_names_a_missing_column_or_table_stops_the_build(workspace, capsys):
    tmp_path, add = workspace
    add({**FLEX_MAPPING, "fuel_material": {"column": "Fuel type"}}, FLEX_CSV)
    assert tt.run(argparse.Namespace()) == 1
    assert "no column named 'Fuel type'" in capsys.readouterr().out
    (tmp_path / "mappings" / "FLEX.csv.yaml").write_text(yaml.safe_dump({**FLEX_MAPPING, "table": "Nope.csv"}))
    assert tt.run(argparse.Namespace()) == 1
    assert "expected one table ending in 'Nope.csv'" in capsys.readouterr().out


def test_real_mapping_files_are_well_formed():
    mappings, skipped = tt.load_mappings()
    assert len(mappings) >= 14 and len(skipped) == 8
    for mapping in mappings:
        assert mapping["fuel_phase"] in ("solid", "liquid", "gas", "other"), mapping["_file"]
        if mapping.get("kind") == "document":
            assert mapping["source"] and all("quote" in row and "page" in row for row in mapping["rows"])
            continue
        assert "label" in mapping and "table" in mapping, mapping["_file"]
        for target in (mapping.get("outcome") or {}).get("map", {}).values():
            assert target in tt.OUTCOMES, mapping["_file"]
    assert all(entry["reason"] for entry in skipped)


def test_exact_duplicate_rows_are_dropped_once(workspace):
    _, add = workspace
    text = FLEX_CSV.replace("2,Heptane,380,0.18,0.52,0.30,~9,Completion\r\n",
                            "2,Heptane,380,0.18,0.52,0.30,~9,Completion\r\n" * 3)
    rows, report = rows_of(FLEX_MAPPING, add(FLEX_MAPPING, text))
    assert [r["test_label"] for r in rows] == ["1", "2", "2"]  # the third row differs (pressure 0)
    assert report["duplicate_rows_dropped"] == 2 and report["skipped_rows"] == 1
    assert rows[1]["source"]["row"] == 2  # the first copy keeps its own row number


def test_cell_with_several_readings_keeps_the_first_and_says_so(workspace):
    _, add = workspace
    mapping = {**SAFFIRE_MAPPING, "flow_cm_s": {"column": "Air Flow (cm/s)", "parse": "first_number",
                                                 "note_if_varied": "Air flow (cm/s)"}}
    text = SAFFIRE_CSV.replace("S1,SIBAL Fabric,20 cm/s", "S1,SIBAL Fabric,5 4 3 2 1").replace(
        "S2,SIBAL Fabric,20 cm/s", "S2,SIBAL Fabric,10")
    rows, _ = rows_of(mapping, add(mapping, text))
    assert rows[0]["flow_cm_s"] == 5.0 and "Air flow (cm/s) recorded as “5 4 3 2 1”" in rows[0]["note"]
    assert rows[1]["flow_cm_s"] == 10.0 and "recorded as" not in rows[1]["note"]


DOCUMENT = {
    "kind": "document", "investigation": "PSI-20", "source": "PSI-20_Reports_Final.pdf", "fuel_phase": "gas",
    "fuel_material": "CH4 burner", "diluent": "N2", "note": "One row per atmosphere.",
    "rows": [{"label": "atmosphere 34 % O2, 8.2 psia", "o2_pct": 34, "pressure_psia": 8.2, "page": 5,
              "quote": "8.2 psia, 34 % oxygen", "outcome": "sustained", "outcome_page": 6,
              "outcome_quote": "the flames generally persisted"}],
}


@pytest.fixture
def report_text(tmp_path, monkeypatch):
    from pipeline import extract_text
    folder = tmp_path / "text"
    folder.mkdir()
    pages = {5: "Atmospheres: 14.7 psia, 21 % oxygen; and 8.2  psia, 34 % oxygen.",
             6: "At 34% oxygen, 8.2 psia, the flames generally persisted for 3 minutes."}
    (folder / "PSI-20_Reports_Final.pdf.jsonl").write_text("\n".join(
        json.dumps({"source_id": "PSI-20_Reports_Final.pdf", "page": n, "text": t}) for n, t in pages.items()))
    monkeypatch.setattr(extract_text, "TEXT_DIR", folder)


def test_document_rows_are_checked_against_the_quoted_page(report_text):
    rows, report = tt.apply_document(DOCUMENT)
    row = rows[0]
    assert (row["o2_pct"], row["pressure_kpa"], row["outcome"]) == (34.0, 56.537, "sustained")
    assert row["source"] == {"file": "PSI-20_Reports_Final.pdf", "row": 5}
    assert "p. 5, “8.2 psia, 34 % oxygen”" in row["note"] and row["extra"]["row_kind"] == "atmosphere"
    assert report["rows"] == 1


@pytest.mark.parametrize("change, message", [
    ({"quote": "8.2 psia, 35 % oxygen"}, "quote not found"),
    ({"o2_pct": 35}, "35 is not in the quote"),
    ({"page": 6}, "quote not found"),
    ({"outcome_quote": "flames always went out"}, "quote not found"),
])
def test_a_document_row_that_does_not_match_its_page_stops_the_build(report_text, change, message):
    bad = {**DOCUMENT, "rows": [{**DOCUMENT["rows"][0], **change}]}
    with pytest.raises(tt.DocumentError, match=message):
        tt.apply_document(bad)
