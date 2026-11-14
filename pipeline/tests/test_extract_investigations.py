import argparse
import csv
import json

import pytest

from pipeline import extract_investigations as ei
from pipeline import llm

RECORDS = {
    "PSI-25": {
        "accession": "PSI-25",
        "investigationAcronym": "BASS-II",
        "title": "Burning and  Suppression of Solids - II",
        "projectType": "Microgravity Investigation",
        "experimentHardware": "Microgravity Science Glovebox (MSG)",
        "subResearchArea": "Spacecraft Fire Safety",
        "related_glds": "PSI-26",
        "doi": "10.60555/4qc4-de67",
        "objective": "Study the ignition, flame growth, flame spread, and extinction limits for\nsolid fuels.",
        "approach": "Fuel samples were mounted inside a small wind tunnel.",
        "hypothesis": "",
        "researchImpacts": "BASS-II helps engineers select the safest materials.",
        "publications": [{"title": "a"}, {"title": "b"}],
    },
    "PSI-60": {
        "accession": "PSI-60",
        "investigationAcronym": "",
        "title": "Computational Analysis of SPICE Data Sets",
        "projectType": "Ground Investigation",
        "experimentHardware": "",
        "subResearchArea": "Gaseous",
        "related_glds": "PSI-107,PSI-25",
        "doi": "",
        "objective": "Model the smoke point of gas-jet flames.",
        "approach": "", "hypothesis": "", "researchImpacts": "",
        "publications": [],
    },
}
GOOD = {
    "PSI-25": {"role": "prevention", "role_evidence": "flame spread, and extinction limits for solid fuels",
               "fuel_phase": "solid", "fuel_phase_evidence": "extinction limits for solid fuels"},
    "PSI-60": {"role": "fundamentals", "role_evidence": "Model the smoke point of gas-jet flames.",
               "fuel_phase": "gas", "fuel_phase_evidence": "gas-jet flames"},
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    for psi_id, record in RECORDS.items():
        (raw / psi_id).mkdir(parents=True)
        (raw / psi_id / "record.json").write_text(json.dumps(record), encoding="utf-8")
        (raw / psi_id / "files.json").write_text("{}", encoding="utf-8")
    inventory_csv = tmp_path / "inventory.csv"
    inventory_csv.write_text("psi_id,hardware_family\nPSI-25,msg_flow_duct\nPSI-60,ground_study\n", encoding="utf-8")
    monkeypatch.setattr(ei.config, "RAW_DIR", raw)
    monkeypatch.setattr(ei.config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ei.inventory, "INVENTORY_CSV", inventory_csv)
    monkeypatch.setattr(ei, "TAGS", tmp_path / "review" / "investigation_tags.json")
    monkeypatch.setattr(ei, "OVERRIDES", tmp_path / "data" / "overrides.json")
    monkeypatch.setattr(ei, "REVIEW_CSV", tmp_path / "review" / "investigations.csv")
    monkeypatch.setattr(ei, "OUT", tmp_path / "out" / "investigations.json")
    monkeypatch.setattr(ei.time, "sleep", lambda seconds: None)
    return tmp_path


def use_ai(monkeypatch, replies):
    """Install a fake AI whose reply depends on which investigation the prompt names."""
    def responder(prompt, schema):
        psi_id = prompt.split("\n", 1)[0].split()[-1]
        reply = replies[psi_id]
        return reply(prompt) if callable(reply) else reply

    fake = llm.FakeProvider(responder)
    monkeypatch.setattr(ei.llm, "get_provider", lambda: fake)
    return fake


def output(workspace):
    return json.loads((workspace / "out" / "investigations.json").read_text(encoding="utf-8"))


def test_quote_found_ignores_case_spacing_and_quote_style():
    source = "Objectives: Study the “flame spread” – and  extinction\nlimits."
    assert ei.quote_found('study the "flame spread" - and extinction limits', source)
    assert ei.quote_found("Extinction limits.", source)
    assert not ei.quote_found("flame spread and extinction", source)  # words were dropped
    assert not ei.quote_found("   ", source)


def test_build_copies_psi_fields_and_adds_tags(workspace, monkeypatch):
    use_ai(monkeypatch, GOOD)
    assert ei.run(argparse.Namespace()) == 0
    data = output(workspace)
    assert data["meta"]["roles_reviewed"] is False and data["meta"]["tags_with_unverified_evidence"] == []
    bass, ground = data["investigations"]
    assert bass == {
        "id": "PSI-25", "acronym": "BASS-II", "title": "Burning and Suppression of Solids - II",
        "project_type": "flight", "role": "prevention", "fuel_phase": "solid",
        "hardware": "Microgravity Science Glovebox (MSG)", "related": ["PSI-26"],
        "doi": "10.60555/4qc4-de67",
        "url": "https://psi.nasa.gov/physci/repo/data/investigations/PSI-25",
        "hardware_family": "msg_flow_duct", "sub_area": "Spacecraft Fire Safety",
        "objectives": "Study the ignition, flame growth, flame spread, and extinction limits for solid fuels.",
        "earth_benefits": "BASS-II helps engineers select the safest materials.", "n_publications": 2,
    }
    assert (ground["acronym"], ground["hardware"], ground["doi"], ground["earth_benefits"]) == (None,) * 4
    assert ground["project_type"] == "ground" and ground["related"] == ["PSI-107", "PSI-25"]


def test_second_run_does_not_call_the_ai_and_retag_does(workspace, monkeypatch):
    fake = use_ai(monkeypatch, GOOD)
    ei.run(argparse.Namespace())
    assert fake.calls == 2
    first = (workspace / "out" / "investigations.json").read_text(encoding="utf-8")

    monkeypatch.setattr(ei.llm, "get_provider", lambda: pytest.fail("the AI should not be needed"))
    assert ei.run(argparse.Namespace()) == 0
    assert (workspace / "out" / "investigations.json").read_text(encoding="utf-8") == first

    monkeypatch.setattr(ei.llm, "get_provider", lambda: fake)
    ei.run(argparse.Namespace(retag=True))
    assert fake.calls == 4  # --retag really asks again; it does not reuse the cached replies


def test_invented_quote_is_asked_again_then_flagged(workspace, monkeypatch, capsys):
    prompts = []

    def stubborn(prompt):
        prompts.append(prompt)
        return {**GOOD["PSI-25"], "role_evidence": "BASS-II proves materials never burn in space"}

    use_ai(monkeypatch, {**GOOD, "PSI-25": stubborn})
    assert ei.run(argparse.Namespace()) == 0
    assert len(prompts) == 2 and "copied exactly" in prompts[1] and "copied exactly" not in prompts[0]
    tags = json.loads((workspace / "review" / "investigation_tags.json").read_text(encoding="utf-8"))
    assert tags["PSI-25"]["role_evidence_found"] is False and tags["PSI-25"]["fuel_phase_evidence_found"] is True
    assert output(workspace)["meta"]["tags_with_unverified_evidence"] == ["PSI-25.role"]
    assert "quote not found: PSI-25.role" in capsys.readouterr().out


def test_quote_fixed_on_the_second_ask_is_accepted(workspace, monkeypatch):
    calls = []

    def learns(prompt):
        calls.append(prompt)
        return GOOD["PSI-25"] if len(calls) > 1 else {**GOOD["PSI-25"], "fuel_phase_evidence": "made up"}

    use_ai(monkeypatch, {**GOOD, "PSI-25": learns})
    ei.run(argparse.Namespace())
    assert output(workspace)["meta"]["tags_with_unverified_evidence"] == []


def test_reply_outside_the_allowed_values_stops_the_run(workspace, monkeypatch, capsys):
    use_ai(monkeypatch, {**GOOD, "PSI-60": {**GOOD["PSI-60"], "role": "safety"}})
    assert ei.run(argparse.Namespace()) == 1
    assert "PSI-60 failed" in capsys.readouterr().out
    # the investigation that did succeed is kept, so the next run only repeats the failed one
    assert list(json.loads((workspace / "review" / "investigation_tags.json").read_text(encoding="utf-8"))) == ["PSI-25"]


def read_review(workspace):
    with (workspace / "review" / "investigations.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_review(workspace, rows):
    with (workspace / "review" / "investigations.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=ei.REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_review_round_trip(workspace, monkeypatch, capsys):
    use_ai(monkeypatch, GOOD)
    ei.run(argparse.Namespace())
    assert ei.run_review_export(argparse.Namespace()) == 0
    rows = read_review(workspace)
    assert [(r["psi_id"], r["field"], r["ai_value"], r["evidence_found"]) for r in rows] == [
        ("PSI-25", "role", "prevention", "yes"), ("PSI-25", "fuel_phase", "solid", "yes"),
        ("PSI-60", "role", "fundamentals", "yes"), ("PSI-60", "fuel_phase", "gas", "yes"),
    ]

    # The reviewer agrees with three tags and corrects one.
    rows[0]["agree"] = "y"
    rows[1]["agree"] = "Yes"
    rows[2]["corrected_value"] = " Prevention "
    rows[2]["reviewer_note"] = "smoke point is a flammability measure"
    write_review(workspace, rows)
    assert ei.run_apply_overrides(argparse.Namespace()) == 0
    overrides = json.loads((workspace / "data" / "overrides.json").read_text(encoding="utf-8"))
    assert overrides == {"roles_reviewed": False, "investigations": {"PSI-60": {"role": "prevention"}}}
    data = output(workspace)
    assert data["investigations"][1]["role"] == "prevention" and data["meta"]["roles_reviewed"] is False
    assert data["meta"]["tags_corrected_by_reviewer"] == 1

    # Exporting again keeps what the reviewer typed; finishing the sheet completes the review.
    assert ei.run_review_export(argparse.Namespace()) == 0
    rows = read_review(workspace)
    assert rows[2]["corrected_value"] == " Prevention " and rows[2]["reviewer_note"].startswith("smoke point")
    assert "3 reviewed" in capsys.readouterr().out
    rows[3]["agree"] = "x"
    write_review(workspace, rows)
    assert ei.run_apply_overrides(argparse.Namespace()) == 0
    assert output(workspace)["meta"]["roles_reviewed"] is True

    # A rebuild from scratch still applies the correction.
    ei.run(argparse.Namespace(retag=True))
    assert output(workspace)["investigations"][1]["role"] == "prevention"


def test_correction_that_is_not_an_allowed_value_changes_nothing(workspace, monkeypatch, capsys):
    use_ai(monkeypatch, GOOD)
    ei.run(argparse.Namespace())
    ei.run_review_export(argparse.Namespace())
    rows = read_review(workspace)
    rows[0]["corrected_value"] = "fire safety"
    write_review(workspace, rows)
    assert ei.run_apply_overrides(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "PSI-25.role: 'fire safety' is not one of prevention, detection, suppression, fundamentals" in out
    assert not (workspace / "data" / "overrides.json").exists()


def test_reviewer_correction_clears_the_unverified_flag(workspace, monkeypatch):
    use_ai(monkeypatch, {**GOOD, "PSI-25": {**GOOD["PSI-25"], "role_evidence": "not in the text"}})
    ei.run(argparse.Namespace())
    assert output(workspace)["meta"]["tags_with_unverified_evidence"] == ["PSI-25.role"]
    ei.run_review_export(argparse.Namespace())
    rows = read_review(workspace)
    assert rows[0]["evidence_found"] == "NO"
    rows[0]["corrected_value"] = "suppression"
    write_review(workspace, rows)
    ei.run_apply_overrides(argparse.Namespace())
    assert output(workspace)["meta"]["tags_with_unverified_evidence"] == []


def test_steps_explain_what_to_run_first(workspace, monkeypatch, capsys):
    assert ei.run_review_export(argparse.Namespace()) == 1
    assert ei.run_apply_overrides(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "run extract-investigations first" in out and "run review-export first" in out
    monkeypatch.setattr(ei.config, "RAW_DIR", workspace / "empty")
    assert ei.run(argparse.Namespace()) == 1
