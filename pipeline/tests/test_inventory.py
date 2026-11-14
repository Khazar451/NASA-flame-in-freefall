import argparse
import csv

import pytest

from pipeline import inventory, psi


def _file(key, size, category):
    return {"key": key, "bytes_size": size, "directory": False, "files": [], "sortingCategory": category}


RECORD = {
    "accession": "PSI-25",
    "investigationAcronym": "BASS-II",
    "title": "Burning and  Suppression\nof Solids - II",
    "projectType": "Microgravity Investigation",
    "flightProgram": "International Space Station (ISS)",
    "experimentHardware": "Microgravity Science Glovebox (MSG)",
    "subResearchArea": "Spacecraft Fire Safety",
    "doi": "10.60555/4qc4-de67",
    "related_glds": "PSI-26",
    "version": "4",
    "experimentalTable": [{"file_name": "PSI-25_Experimental table_BASS-II.csv", "version": 1}],
    "publications": [{"title": "a"}, {"title": "b"}],
}

TREE = {
    "files": [
        {"displayName": "Reports", "key": "psi-data/PSI-25/Reports/", "files": [
            _file("psi-data/PSI-25/Reports/final.pdf", 2_000_000, "Reports"),
        ]},
        {"displayName": "Science Documents", "key": "psi-data/PSI-25/Science Documents/", "files": [
            _file("psi-data/PSI-25/Science Documents/srd.PDF", 500_000, "Science Documents"),
            _file("psi-data/PSI-25/Science Documents/bundle.zip", 9_000_000, "Science Documents"),
        ]},
        {"displayName": "Raw Data", "key": "psi-data/PSI-25/Raw Data/", "files": [
            {"displayName": "run1", "key": "psi-data/PSI-25/Raw Data/run1/", "directory": True, "files": [
                _file("psi-data/PSI-25/Raw Data/run1/video.mp4", 3_000_000_000, "Raw Data"),
                _file("psi-data/PSI-25/Raw Data/run1/notes.pdf", 1_000_000, "Raw Data"),
            ]},
        ]},
        {"displayName": "Presentations", "key": "psi-data/PSI-25/Presentations/", "files": []},
    ]
}


def test_iter_files_walks_nested_folders_and_skips_empty_ones():
    found = [(category, node["key"].rsplit("/", 1)[-1]) for category, node in psi.iter_files(TREE)]
    assert found == [
        ("Reports", "final.pdf"),
        ("Science Documents", "srd.PDF"),
        ("Science Documents", "bundle.zip"),
        ("Raw Data", "video.mp4"),
        ("Raw Data", "notes.pdf"),
    ]


def test_summarize_counts_only_readable_documents_outside_raw_data():
    row = inventory.summarize(RECORD, TREE)
    assert row["psi_id"] == "PSI-25"
    assert row["title"] == "Burning and Suppression of Solids - II"
    assert row["n_tables"] == 1
    assert row["table_files"] == "PSI-25_Experimental table_BASS-II.csv"
    assert row["n_docs"] == 2  # the zip and the PDF under Raw Data do not count
    assert row["docs_mb"] == 2.5
    assert row["n_publications"] == 2
    assert row["total_gb"] == 3.01
    assert row["file_categories"] == "Raw Data: 2; Reports: 1; Science Documents: 2"
    assert row["hardware_family"] == "msg_flow_duct"
    assert row["url"].endswith("/PSI-25")
    assert set(row) == set(inventory.COLUMNS)


@pytest.mark.parametrize(
    "acronym, project_type, family",
    [
        ("ACME CFI-G", "Microgravity Investigation", "acme_gas_flames"),
        ("CFI", "Microgravity Investigation", "mdca_droplets"),
        ("FLEX-2", "Microgravity Investigation", "mdca_droplets"),
        ("SAFFIRE-III", "Microgravity Investigation", "saffire"),
        ("SAME-R", "Microgravity Investigation", "smoke_aerosol"),
        ("DAFT / DAFT-2", "Microgravity Investigation", "smoke_aerosol"),
        ("", "Ground Investigation", "ground_study"),
        ("NEW-THING", "Microgravity Investigation", ""),
    ],
)
def test_default_family(acronym, project_type, family):
    assert inventory.default_family(acronym, project_type) == family


def test_write_csv_sorts_numerically_and_keeps_hand_edits(tmp_path):
    path = tmp_path / "inventory.csv"
    rows = [
        {**inventory.summarize(RECORD, TREE), "psi_id": "PSI-100"},
        inventory.summarize(RECORD, TREE),
    ]
    inventory.write_csv(rows, path)

    with path.open(newline="", encoding="utf-8") as fh:
        saved = list(csv.DictReader(fh))
    assert [r["psi_id"] for r in saved] == ["PSI-25", "PSI-100"]

    saved[0]["hardware_family"] = "checked_by_me"
    saved[0]["notes"] = "ME: ranges are in the SRD"
    saved[1]["hardware_family"] = ""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=inventory.COLUMNS)
        writer.writeheader()
        writer.writerows(saved)

    inventory.write_csv(
        [inventory.summarize(RECORD, TREE), {**inventory.summarize(RECORD, TREE), "psi_id": "PSI-100"}],
        path,
    )
    with path.open(newline="", encoding="utf-8") as fh:
        again = {r["psi_id"]: r for r in csv.DictReader(fh)}
    assert again["PSI-25"]["hardware_family"] == "checked_by_me"
    assert again["PSI-25"]["notes"] == "ME: ranges are in the SRD"
    assert again["PSI-100"]["hardware_family"] == "msg_flow_duct"  # blank cell gets the default back


def test_run_uses_saved_replies_and_only_combustion(tmp_path, monkeypatch, capsys):
    calls = {"record": 0, "tree": 0}

    def get_record(psi_id):
        calls["record"] += 1
        return {**RECORD, "accession": psi_id}

    def get_file_tree(psi_id, version):
        calls["tree"] += 1
        assert version == "4"
        return TREE

    monkeypatch.setattr(inventory.config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(inventory, "INVENTORY_CSV", tmp_path / "inventory.csv")
    monkeypatch.setattr(inventory.config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(psi, "get_record", get_record)
    monkeypatch.setattr(psi, "get_file_tree", get_file_tree)
    monkeypatch.setattr(
        psi,
        "search_investigations",
        lambda: [
            {"Accession": "PSI-107", "Research Area": "Combustion Science"},
            {"Accession": "PSI-119", "Research Area": "Fluid Physics"},
            {"Accession": "PSI-25", "Research Area": "Combustion Science"},
        ],
    )

    assert inventory.run(argparse.Namespace(refresh=False)) == 0
    assert calls == {"record": 2, "tree": 2}
    assert (tmp_path / "raw" / "PSI-25" / "record.json").exists()
    assert (tmp_path / "raw" / "PSI-107" / "files.json").exists()

    assert inventory.run(argparse.Namespace()) == 0  # `run_all all` passes no --refresh
    assert calls == {"record": 2, "tree": 2}

    assert inventory.run(argparse.Namespace(refresh=True)) == 0
    assert calls == {"record": 4, "tree": 4}
    assert "2 combustion investigations: PSI-25, PSI-107" in capsys.readouterr().out


def test_run_reports_api_failure(monkeypatch, capsys):
    def boom():
        raise psi.PSIError("PSI is down")

    monkeypatch.setattr(psi, "search_investigations", boom)
    assert inventory.run(argparse.Namespace(refresh=False)) == 1
    assert "PSI is down" in capsys.readouterr().out
