import argparse
import json

import pytest

from pipeline import fetch, psi


def node(key, size, category, version=1, **more):
    return {"key": key, "bytes_size": size, "directory": False, "files": [],
            "sortingCategory": category, "version": version, **more}


TREE = {
    "files": [
        {"displayName": "Experimental table", "files": [
            node("psi-data/PSI-25/Experimental table/PSI-25_Experimental table_BASS-II.csv", 11, "Experimental table"),
        ]},
        {"displayName": "Reports", "files": [
            node("psi-data/PSI-25/Reports/PSI-25_Reports_Summary.PDF", 7, "Reports", version=2),
            node("psi-data/PSI-25/Reports/PSI-25_Reports_Huge.pdf", fetch.MAX_BYTES + 1, "Reports"),
            node("psi-data/PSI-25/Reports/PSI-25_Reports_Private.pdf", 5, "Reports", restricted=True),
            node("psi-data/PSI-25/Reports/PSI-25_Reports_Movie.mp4", 5, "Reports"),
        ]},
        {"displayName": "Engineering Documents", "files": [
            node("psi-data/PSI-25/Engineering Documents/PSI-25_Engineering Documents_photos.zip", 9, "Engineering Documents"),
        ]},
        {"displayName": "Raw Data", "files": [
            {"displayName": "run1", "directory": True, "files": [
                node("psi-data/PSI-25/Raw Data/run1/video.mp4", 3_000_000_000, "Raw Data"),
                node("psi-data/PSI-25/Raw Data/run1/log.pdf", 4, "Raw Data"),
            ]},
        ]},
    ]
}


@pytest.fixture
def raw(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    (raw_dir / "PSI-25").mkdir(parents=True)
    (raw_dir / "PSI-25" / "files.json").write_text(json.dumps(TREE), encoding="utf-8")
    monkeypatch.setattr(fetch.config, "RAW_DIR", raw_dir)
    monkeypatch.setattr(fetch, "MANIFEST", raw_dir / "manifest.json")
    return raw_dir


@pytest.fixture
def downloads(monkeypatch):
    calls = []

    def fake_download(psi_id, file_name, version, dest):
        calls.append((psi_id, file_name, version))
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = b"x" * (11 if file_name.endswith(".csv") else 7)
        dest.write_bytes(data)
        return len(data), "sha-" + file_name

    monkeypatch.setattr(psi, "download", fake_download)
    return calls


def test_select_takes_tables_and_pdfs_and_never_raw_data():
    items, skipped = fetch.select("PSI-25", TREE)
    assert [(i.category, i.path, i.version) for i in items] == [
        ("Experimental table", "PSI-25/tables/PSI-25_Experimental table_BASS-II.csv", 1),
        ("Reports", "PSI-25/docs/PSI-25_Reports_Summary.PDF", 2),
    ]
    assert skipped == {
        "over the size limit": 1,
        "restricted": 1,
        "file type not wanted: .zip": 1,
        "file type not wanted: .mp4": 1,
        "category not wanted: Raw Data": 2,
    }


def test_run_downloads_then_rerun_downloads_nothing(raw, downloads, capsys):
    assert fetch.run(argparse.Namespace(dry_run=False)) == 0
    assert len(downloads) == 2
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    assert [f["path"] for f in manifest["files"]] == [
        "PSI-25/docs/PSI-25_Reports_Summary.PDF",
        "PSI-25/tables/PSI-25_Experimental table_BASS-II.csv",
    ]
    first = manifest["files"][0]
    assert first["sha256"] == "sha-PSI-25_Reports_Summary.PDF" and first["version"] == 2 and first["fetched_at"]
    assert manifest["skipped"]["category not wanted: Raw Data"] == 2
    assert "2 downloaded, 0 already on disk, 0 unavailable at PSI, 0 failed" in capsys.readouterr().out

    assert fetch.run(argparse.Namespace()) == 0
    assert len(downloads) == 2  # nothing fetched again
    assert json.loads((raw / "manifest.json").read_text(encoding="utf-8")) == manifest
    assert "0 downloaded, 2 already on disk, 0 unavailable at PSI, 0 failed" in capsys.readouterr().out


def test_file_already_on_disk_is_hashed_not_downloaded(raw, downloads):
    table = raw / "PSI-25" / "tables" / "PSI-25_Experimental table_BASS-II.csv"
    table.parent.mkdir(parents=True)
    table.write_bytes(b"y" * 11)
    assert fetch.run(argparse.Namespace()) == 0
    assert [name for _, name, _ in downloads] == ["PSI-25_Reports_Summary.PDF"]
    entry = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))["files"][1]
    assert len(entry["sha256"]) == 64


def test_network_failure_fails_the_run_but_a_404_does_not(raw, monkeypatch, capsys):
    def download(psi_id, file_name, version, dest):
        if file_name.endswith(".csv"):
            raise psi.PSIError("connection dropped")
        raise psi.PSINotFound("missing")

    monkeypatch.setattr(psi, "download", download)
    assert fetch.run(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "0 downloaded, 0 already on disk, 1 unavailable at PSI, 1 failed" in out
    assert "FAILED: PSI-25/tables/PSI-25_Experimental table_BASS-II.csv: connection dropped" in out
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == []
    assert [u["path"] for u in manifest["unavailable"]] == ["PSI-25/docs/PSI-25_Reports_Summary.PDF"]

    monkeypatch.setattr(psi, "download", lambda *a: (_ for _ in ()).throw(psi.PSINotFound("missing")))
    assert fetch.run(argparse.Namespace()) == 0  # only 404s: recorded, not a failure


def test_size_different_from_listing_is_kept_and_not_refetched(raw, monkeypatch):
    calls = []

    def download(psi_id, file_name, version, dest):
        calls.append(file_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 20)  # PSI lists 11 and 7
        return 20, "sha"

    monkeypatch.setattr(psi, "download", download)
    assert fetch.run(argparse.Namespace()) == 0
    files = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert [(f["bytes"], f["listed_bytes"]) for f in files] == [(20, 7), (20, 11)]
    assert fetch.run(argparse.Namespace()) == 0
    assert len(calls) == 2


def test_word_and_powerpoint_files_are_wanted():
    names = ["a.docx", "b.DOC", "c.pptx", "d.ppt", "e.xlsx"]
    tree = {"files": [{"displayName": "Science Documents", "files": [
        node(f"psi-data/PSI-25/Science Documents/{name}", 5, "Science Documents") for name in names
    ]}]}
    items, skipped = fetch.select("PSI-25", tree)
    assert [i.file_name for i in items] == names[:4]
    assert skipped == {"file type not wanted: .xlsx": 1}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("PSI-25_Reports_Summary.pdf", "PSI-25_Reports_Summary.pdf"),
        ("PSI-10_Engineering Documents_Rpt..pdf", "PSI-10_Engineering Documents_Rpt..pdf"),
        ('a/b\\c:d*e?f"g<h>i|j.pdf', "a_b_c_d_e_f_g_h_i_j.pdf"),
    ],
)
def test_local_name_is_safe_on_windows(name, expected):
    assert fetch.local_name(name) == expected


def test_local_name_shortens_long_names_without_collisions():
    first = fetch.local_name("PSI-22_Presentations_" + "x" * 300 + " June 2012.pdf")
    second = fetch.local_name("PSI-22_Presentations_" + "x" * 300 + " July 2012.pdf")
    assert len(first) == fetch.MAX_NAME_LENGTH and first.endswith(".pdf")
    assert first != second and first[:100] == second[:100]
    assert fetch.local_name(first) == first  # already short enough: unchanged


def test_file_name_containing_a_slash():
    tree = {"files": [{"displayName": "Engineering Documents", "files": [
        {"displayName": "PSI-22_Engineering Documents_rev_A - ACME", "directory": True, "files": [
            node("psi-data/PSI-22/Engineering Documents/PSI-22_Engineering Documents_rev_A - ACME/CIR Schematic.pdf",
                 5, "Engineering Documents"),
        ]},
    ]}]}
    items, _ = fetch.select("PSI-22", tree)
    assert items[0].file_name == "PSI-22_Engineering Documents_rev_A - ACME/CIR Schematic.pdf"
    assert items[0].path == "PSI-22/docs/PSI-22_Engineering Documents_rev_A - ACME_CIR Schematic.pdf"


def test_dry_run_downloads_nothing(raw, downloads, capsys):
    assert fetch.run(argparse.Namespace(dry_run=True)) == 0
    assert downloads == [] and not (raw / "manifest.json").exists()
    assert "2 files wanted" in capsys.readouterr().out


def test_run_without_inventory_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch.config, "RAW_DIR", tmp_path)
    assert fetch.run(argparse.Namespace()) == 1
    assert "run the inventory step first" in capsys.readouterr().out
