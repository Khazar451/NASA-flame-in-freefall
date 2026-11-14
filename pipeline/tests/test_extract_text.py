import argparse
import json
import zipfile

import pytest

from pipeline import extract_text, fetch

RECORD = {
    "accession": "PSI-25",
    "title": "Burning and Suppression of Solids - II",
    "objective": "Study  flame spread\nin low-velocity flows.",
    "approach": "A small wind tunnel in the glovebox.",
    "hypothesis": "",
    "researchImpacts": "Improved spacecraft fire safety.",
    "publications": [
        {"authorList": "Olson S., Ferkul P.", "title": "Flammability boundary", "doi": "10.1016/x"},
        {"authorList": "", "title": "No authors listed", "doi": ""},
    ],
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    (raw / "PSI-25" / "docs").mkdir(parents=True)
    (raw / "PSI-25" / "files.json").write_text("{}")
    (raw / "PSI-25" / "record.json").write_text(json.dumps(RECORD))
    entries = []
    for name, sha in (("PSI-25_Reports_Summary.pdf", "sha-a"), ("PSI-25_Reports_Scan.pdf", "sha-b")):
        (raw / "PSI-25" / "docs" / name).write_bytes(b"%PDF")
        entries.append({"psi_id": "PSI-25", "category": "Reports", "file_name": name, "version": 1,
                        "bytes": 4, "path": f"PSI-25/docs/{name}", "sha256": sha, "fetched_at": "now"})
    entries.append({"psi_id": "PSI-25", "category": "Experimental table", "file_name": "PSI-25_table.csv",
                    "version": 1, "bytes": 4, "path": "PSI-25/tables/PSI-25_table.csv", "sha256": "sha-c",
                    "fetched_at": "now"})
    (raw / "manifest.json").write_text(json.dumps({"files": entries, "skipped": {}}))

    text_dir = tmp_path / "interim" / "text"
    monkeypatch.setattr(fetch.config, "RAW_DIR", raw)
    monkeypatch.setattr(fetch, "MANIFEST", raw / "manifest.json")
    monkeypatch.setattr(extract_text, "TEXT_DIR", text_dir)
    monkeypatch.setattr(extract_text, "CONVERTED_DIR", tmp_path / "interim" / "converted")
    monkeypatch.setattr(extract_text, "INDEX", text_dir / "index.json")
    return raw, text_dir


@pytest.fixture
def reader(monkeypatch):
    calls = []

    def fake_pages(path, engine=None):
        calls.append(path.name)
        if "Scan" in path.name:
            return ["", "", "3"]
        return [
            "Oxygen was varied below 21% by adding nitrogen to the working volume of the glovebox. " * 3,
            "",
            "Flow speeds up to 40 cm/s.",
        ]

    monkeypatch.setattr(extract_text, "pdf_pages", fake_pages)
    monkeypatch.setattr(extract_text, "pdf_engine", lambda: "fake")
    return calls


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_pdf_pages_keep_their_page_numbers(workspace, reader, capsys):
    _, text_dir = workspace
    assert extract_text.run(argparse.Namespace()) == 0
    lines = read_jsonl(text_dir / "PSI-25_Reports_Summary.pdf.jsonl")
    assert [line["page"] for line in lines] == [1, 2, 3]
    assert lines[2] == {
        "source_id": "PSI-25_Reports_Summary.pdf", "psi_id": "PSI-25", "category": "Reports",
        "page": 3, "text": "Flow speeds up to 40 cm/s.",
    }
    index = json.loads((text_dir / "index.json").read_text())
    assert index["PSI-25_Reports_Summary.pdf"]["pages"] == 3
    assert index["PSI-25_Reports_Summary.pdf"]["empty_pages"] == 1
    assert index["PSI-25_Reports_Summary.pdf"]["scanned"] is False
    assert index["PSI-25_Reports_Scan.pdf"]["scanned"] is True
    assert not (text_dir / "PSI-25_table.csv.jsonl").exists()  # tables are not text sources
    out = capsys.readouterr().out
    assert "extract-text (fake): 2 documents read, 0 unchanged, 0 unreadable, 1 PSI records; 6 pages" in out
    assert "scanned: PSI-25_Reports_Scan.pdf" in out and "scanned: PSI-25_Reports_Summary.pdf" not in out


def test_record_becomes_sections_and_skips_empty_ones(workspace, reader):
    _, text_dir = workspace
    extract_text.run(argparse.Namespace())
    lines = read_jsonl(text_dir / "PSI-25_record.jsonl")
    assert [line["section"] for line in lines] == ["title", "objectives", "approach", "earth_benefits", "publications"]
    by_section = {line["section"]: line for line in lines}
    assert by_section["objectives"]["text"] == "Study flame spread in low-velocity flows."
    assert by_section["objectives"]["source_id"] == "PSI-25_record" and by_section["objectives"]["page"] is None
    assert by_section["publications"]["text"] == (
        "Olson S., Ferkul P.. Flammability boundary. 10.1016/x\nNo authors listed"
    )


def test_rerun_reads_only_changed_pdfs(workspace, reader, capsys):
    raw, text_dir = workspace
    extract_text.run(argparse.Namespace())
    first_index = (text_dir / "index.json").read_text()
    assert len(reader) == 2

    extract_text.run(argparse.Namespace())
    assert len(reader) == 2
    assert (text_dir / "index.json").read_text() == first_index
    assert "0 documents read, 2 unchanged" in capsys.readouterr().out

    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["files"][0]["sha256"] = "sha-new"
    (raw / "manifest.json").write_text(json.dumps(manifest))
    extract_text.run(argparse.Namespace())
    assert reader[2:] == ["PSI-25_Reports_Summary.pdf"]


def test_unreadable_pdf_is_recorded_and_not_retried(workspace, monkeypatch, capsys):
    _, text_dir = workspace
    calls = []

    def pages(path, engine=None):
        calls.append(path.name)
        if "Scan" in path.name:
            raise ValueError("cannot open\nbroken document")
        return ["some text that is long enough to count as a real text layer for this page"]

    monkeypatch.setattr(extract_text, "pdf_pages", pages)
    monkeypatch.setattr(extract_text, "pdf_engine", lambda: "fake")
    assert extract_text.run(argparse.Namespace()) == 0
    assert (text_dir / "PSI-25_Reports_Summary.pdf.jsonl").exists()
    assert not (text_dir / "PSI-25_Reports_Scan.pdf.jsonl").exists()
    index = json.loads((text_dir / "index.json").read_text())
    assert index["PSI-25_Reports_Scan.pdf"]["error"] == "ValueError: cannot open broken document"
    assert "unreadable: PSI-25_Reports_Scan.pdf: ValueError" in capsys.readouterr().out

    assert extract_text.run(argparse.Namespace()) == 0
    assert len(calls) == 2  # the broken file is not read again
    assert "1 unreadable" in capsys.readouterr().out


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def make_docx(path, body):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}"><w:body>{body}</w:body></w:document>',
        )


def make_pptx(path, slides, order):
    """`slides` maps a slide file number to its paragraphs; `order` is the presentation order."""
    with zipfile.ZipFile(path, "w") as archive:
        ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{n}"/>' for n in order)
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}"><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>',
        )
        rels = "".join(f'<Relationship Id="rId{n}" Target="slides/slide{n}.xml"/>' for n in slides)
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        for n, paragraphs in slides.items():
            body = "".join(f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>" for text in paragraphs)
            archive.writestr(
                f"ppt/slides/slide{n}.xml",
                f'<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}"><p:cSld><p:spTree><p:sp><p:txBody>{body}'
                "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
            )


def add_to_manifest(raw, name, category, sha, content=b"x"):
    (raw / "PSI-25" / "docs" / name).write_bytes(content)
    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["files"].append({"psi_id": "PSI-25", "category": category, "file_name": name, "version": 1,
                              "bytes": len(content), "path": f"PSI-25/docs/{name}", "sha256": sha,
                              "fetched_at": "now"})
    (raw / "manifest.json").write_text(json.dumps(manifest))


def test_docx_text_in_reading_order_including_tables_and_text_boxes(tmp_path):
    path = tmp_path / "readme.docx"
    make_docx(path, (
        "<w:p><w:r><w:t>Oxygen</w:t></w:r><w:r><w:tab/><w:t>21 %</w:t><w:br/><w:t>by volume</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>   </w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Flow</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>40 cm/s</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>Outer</w:t></w:r><w:r><mc:AlternateContent>"
        "<mc:Choice><w:txbxContent><w:p><w:r><w:t>In a text box</w:t></w:r></w:p></w:txbxContent></mc:Choice>"
        "<mc:Fallback><w:txbxContent><w:p><w:r><w:t>In a text box</w:t></w:r></w:p></w:txbxContent></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    ))
    assert extract_text.docx_parts(path) == ["Oxygen\t21 %\nby volume\nFlow\n40 cm/s\nOuter\nIn a text box"]


def test_docx_is_cut_into_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_text, "DOCX_PART_CHARS", 25)
    path = tmp_path / "long.docx"
    make_docx(path, "".join(f"<w:p><w:r><w:t>paragraph {n} text</w:t></w:r></w:p>" for n in range(1, 5)))
    assert extract_text.docx_parts(path) == [
        "paragraph 1 text", "paragraph 2 text", "paragraph 3 text", "paragraph 4 text",
    ]
    make_docx(path, "")
    assert extract_text.docx_parts(path) == []


def test_pptx_slides_follow_presentation_order_not_file_numbers(tmp_path):
    path = tmp_path / "talk.pptx"
    make_pptx(path, {1: ["Results", "Smoke point 60 mm"], 2: [], 3: ["Title slide"]}, order=[3, 1, 2])
    assert extract_text.pptx_slides(path) == ["Title slide", "Results\nSmoke point 60 mm", ""]


def test_office_files_end_to_end(workspace, reader, monkeypatch):
    raw, text_dir = workspace
    make_docx(raw / "PSI-25" / "docs" / "PSI-25_Science Documents_ReadMe.docx",
              "<w:p><w:r><w:t>Oxygen was varied below 21 percent.</w:t></w:r></w:p>")
    make_pptx(raw / "PSI-25" / "docs" / "PSI-25_Presentations_Overview.pptx", {1: ["Slide one"], 2: ["Slide two"]}, [1, 2])
    for name, category, sha in (
        ("PSI-25_Science Documents_ReadMe.docx", "Science Documents", "sha-d"),
        ("PSI-25_Presentations_Overview.pptx", "Presentations", "sha-e"),
        ("PSI-25_Reports_Old.doc", "Reports", "sha-f"),
    ):
        path = raw / "PSI-25" / "docs" / name
        add_to_manifest(raw, name, category, sha, path.read_bytes() if path.exists() else b"x")
    converted = []

    def fake_convert(path, out_dir):
        converted.append(path.name)
        return out_dir / (path.stem + ".pdf")

    monkeypatch.setattr(extract_text, "office_to_pdf", fake_convert)
    assert extract_text.run(argparse.Namespace()) == 0

    docx = read_jsonl(text_dir / "PSI-25_Science Documents_ReadMe.docx.jsonl")
    assert docx == [{"source_id": "PSI-25_Science Documents_ReadMe.docx", "psi_id": "PSI-25",
                     "category": "Science Documents", "page": None, "section": "part 1",
                     "text": "Oxygen was varied below 21 percent."}]
    pptx = read_jsonl(text_dir / "PSI-25_Presentations_Overview.pptx.jsonl")
    assert [(line["page"], line["text"]) for line in pptx] == [(1, "Slide one"), (2, "Slide two")]

    assert converted == ["PSI-25_Reports_Old.doc"]  # only the old format needs LibreOffice
    assert reader[-1] == "PSI-25_Reports_Old.pdf"
    index = json.loads((text_dir / "index.json").read_text())
    assert index["PSI-25_Science Documents_ReadMe.docx"]["engine"] == "docx"
    assert index["PSI-25_Science Documents_ReadMe.docx"]["paged"] is False
    assert index["PSI-25_Presentations_Overview.pptx"]["engine"] == "pptx"
    assert index["PSI-25_Reports_Old.doc"]["engine"] == "libreoffice+fake"
    assert "paged" not in index["PSI-25_Reports_Summary.pdf"]

    assert extract_text.run(argparse.Namespace()) == 0
    assert converted == ["PSI-25_Reports_Old.doc"]  # nothing is read twice


def test_old_office_file_without_libreoffice_is_recorded_not_fatal(workspace, reader, monkeypatch, capsys):
    raw, text_dir = workspace
    add_to_manifest(raw, "PSI-25_Reports_Old.doc", "Reports", "sha-e")
    make_docx(raw / "PSI-25" / "docs" / "PSI-25_Reports_New.docx", "<w:p><w:r><w:t>Still readable.</w:t></w:r></w:p>")
    add_to_manifest(raw, "PSI-25_Reports_New.docx", "Reports", "sha-g",
                    (raw / "PSI-25" / "docs" / "PSI-25_Reports_New.docx").read_bytes())
    monkeypatch.setattr(extract_text, "find_soffice", lambda: None)
    assert extract_text.run(argparse.Namespace()) == 0
    index = json.loads((text_dir / "index.json").read_text())
    assert "install LibreOffice" in index["PSI-25_Reports_Old.doc"]["error"]
    assert index["PSI-25_Reports_New.docx"]["chars"] == len("Still readable.")  # needs nothing installed
    assert "unreadable: PSI-25_Reports_Old.doc" in capsys.readouterr().out


def test_file_with_no_pages_or_text_is_listed_as_unreadable(workspace, reader, capsys):
    raw, text_dir = workspace
    make_docx(raw / "PSI-25" / "docs" / "PSI-25_Reports_Picture.docx", "<w:p><w:r><w:drawing/></w:r></w:p>")
    add_to_manifest(raw, "PSI-25_Reports_Picture.docx", "Reports", "sha-h",
                    (raw / "PSI-25" / "docs" / "PSI-25_Reports_Picture.docx").read_bytes())
    assert extract_text.run(argparse.Namespace()) == 0
    index = json.loads((text_dir / "index.json").read_text())
    assert index["PSI-25_Reports_Picture.docx"]["error"] == "ValueError: the file has no readable pages or text"
    assert not (text_dir / "PSI-25_Reports_Picture.docx.jsonl").exists()
    assert "1 unreadable" in capsys.readouterr().out


def test_find_soffice_checks_standard_install_locations(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_text.shutil, "which", lambda name: None)
    monkeypatch.setattr(extract_text, "SOFFICE_LOCATIONS", (str(tmp_path / "missing"),))
    assert extract_text.find_soffice() is None
    program = tmp_path / "soffice.exe"
    program.write_bytes(b"")
    monkeypatch.setattr(extract_text, "SOFFICE_LOCATIONS", (str(tmp_path / "missing"), str(program)))
    assert extract_text.find_soffice() == str(program)


def test_source_with_a_slash_in_its_name_gets_a_flat_file(workspace, reader):
    raw, text_dir = workspace
    name = "PSI-25_Engineering Documents_rev_A - ACME/CIR: Schematic.pdf"
    flat = fetch.local_name(name)
    assert flat == "PSI-25_Engineering Documents_rev_A - ACME_CIR_ Schematic.pdf"
    (raw / "PSI-25" / "docs" / flat).write_bytes(b"%PDF")
    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["files"].append({"psi_id": "PSI-25", "category": "Engineering Documents", "file_name": name,
                              "version": 1, "bytes": 4, "path": f"PSI-25/docs/{flat}", "sha256": "sha-f",
                              "fetched_at": "now"})
    (raw / "manifest.json").write_text(json.dumps(manifest))
    assert extract_text.run(argparse.Namespace()) == 0
    assert read_jsonl(text_dir / f"{flat}.jsonl")[0]["source_id"] == name
    assert [p.name for p in text_dir.iterdir() if p.is_dir()] == []


def test_real_libreoffice_conversion(tmp_path):
    try:
        engine = extract_text.pdf_engine()
    except RuntimeError:
        pytest.skip("no PDF reader is installed")
    if not extract_text.find_soffice():
        pytest.skip("LibreOffice is not installed")
    source = tmp_path / "note.txt"
    source.write_text("Oxygen 30 percent at 10.2 psi")
    pdf = extract_text.office_to_pdf(source, tmp_path / "converted")
    assert extract_text.pdf_pages(pdf, engine) == ["Oxygen 30 percent at 10.2 psi"]


# A minimal two-page PDF, so the real readers are tested without needing one to write it.
def two_page_pdf(path):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
    ]
    for number, text in ((4, b"Oxygen 21 percent"), (6, b"Pressure 14.7 psi")):
        stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 7 0 R >> >> >>" % number
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(out)


@pytest.mark.parametrize("engine", ["pymupdf", "pdftotext"])
def test_real_pdf_round_trip(tmp_path, engine):
    if engine == "pymupdf":
        pytest.importorskip("pymupdf")
    elif not extract_text.shutil.which("pdftotext"):
        pytest.skip("pdftotext is not installed")
    path = tmp_path / "two_pages.pdf"
    two_page_pdf(path)
    assert extract_text.pdf_pages(path, engine) == ["Oxygen 21 percent", "Pressure 14.7 psi"]


def test_run_with_nothing_downloaded_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch.config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch, "MANIFEST", tmp_path / "manifest.json")
    assert extract_text.run(argparse.Namespace()) == 1
    assert "run the inventory and fetch steps first" in capsys.readouterr().out
