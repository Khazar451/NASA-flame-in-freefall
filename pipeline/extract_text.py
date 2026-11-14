"""Phase 3: turn downloaded documents and PSI records into page-numbered text.

Writes one JSON-lines file per source into data/interim/text/:
  <file name>.jsonl      one line per page: {source_id, psi_id, category, page, text}
  <psi_id>_record.jsonl  one line per section of the PSI record (objectives, approach, ...)
plus index.json, which lists every source with its page and character counts.

`source_id` is the PSI file name (it already starts with the PSI id), so an answer can
cite "PSI-25_Reports_BASS-II Summary Report.pdf, page 12".

How each file type is read (all of it runs the same on Windows, macOS and Linux):
  .pdf          PyMuPDF if installed, otherwise the pdftotext command
  .pptx         read directly; the page number is the slide number
  .docx         read directly; a Word file has no fixed pages, so the text is cut into
                parts of about 3,000 characters and cited as "part 3" (page is null)
  .doc, .ppt    the old binary formats need LibreOffice to convert them to PDF first.
                Without LibreOffice these files are skipped and listed as unreadable.

Scanned PDFs with no text layer are reported, not OCR'd. A file that cannot be read is
recorded in the index with its error and does not fail the run. Safe to re-run:
unchanged files are not read again.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import Any

from pipeline import config, fetch

TEXT_DIR = config.INTERIM_DIR / "text"
INDEX = TEXT_DIR / "index.json"
CONVERTED_DIR = config.INTERIM_DIR / "converted"

LEGACY_OFFICE_EXTENSIONS = {"doc", "ppt"}
READABLE_EXTENSIONS = {"pdf", "docx", "pptx"} | LEGACY_OFFICE_EXTENSIONS
DOCX_PART_CHARS = 3000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# Office stores some content twice: once for new readers, once as a fallback for old ones.
_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
SOFFICE_LOCATIONS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

# PSI record field -> section name in the output
RECORD_SECTIONS = {
    "objective": "objectives",
    "approach": "approach",
    "hypothesis": "hypothesis",
    "researchImpacts": "earth_benefits",
}
# Below this many characters per page on average, a PDF is treated as scanned.
SCANNED_CHARS_PER_PAGE = 50


def pdf_engine() -> str:
    """PyMuPDF when it is installed, otherwise the pdftotext command (poppler-utils)."""
    try:
        import pymupdf  # noqa: F401

        return "pymupdf"
    except ImportError:
        if shutil.which("pdftotext"):
            return "pdftotext"
    raise RuntimeError("No PDF reader: pip install pymupdf, or install poppler-utils for pdftotext")


def pdf_pages(path: Path, engine: str | None = None) -> list[str]:
    """Text of every page of a PDF, in order. Raises on a file that cannot be read."""
    engine = engine or pdf_engine()
    if engine == "pymupdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return [page.get_text("text").strip() for page in doc]
    done = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=300
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip()[:200] or "pdftotext failed")
    pages = done.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():  # pdftotext ends every page, including the last, with \f
        pages.pop()
    return [page.strip() for page in pages]


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        for child in node:
            if child.tag in (_W + "p", _FALLBACK):  # nested paragraphs are read on their own
                continue
            if child.tag == _W + "t":
                parts.append(child.text or "")
            elif child.tag == _W + "tab":
                parts.append("\t")
            elif child.tag in (_W + "br", _W + "cr"):
                parts.append("\n")
            else:
                visit(child)

    visit(paragraph)
    return "".join(parts).strip()


def docx_parts(path: Path) -> list[str]:
    """Text of a .docx file, in reading order, cut into parts of about DOCX_PART_CHARS."""
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        for child in node:
            if child.tag == _FALLBACK:
                continue
            if child.tag == _W + "p":
                text = _docx_paragraph_text(child)
                if text:
                    paragraphs.append(text)
            visit(child)  # tables and text boxes hold further paragraphs

    visit(root)
    parts: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        if current and sum(map(len, current)) + len(paragraph) > DOCX_PART_CHARS:
            parts.append("\n".join(current))
            current = []
        current.append(paragraph)
    if current:
        parts.append("\n".join(current))
    return parts


def pptx_slides(path: Path) -> list[str]:
    """Text of every slide of a .pptx file, in presentation order."""
    with zipfile.ZipFile(path) as archive:
        presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
        relations = ElementTree.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        target = {rel.get("Id"): rel.get("Target") or "" for rel in relations}
        slides: list[str] = []
        for slide_id in presentation.iter(_P + "sldId"):
            name = target[slide_id.get(_R + "id")]
            name = name.lstrip("/") if name.startswith("/") else f"ppt/{name}"
            slide = ElementTree.fromstring(archive.read(name))
            paragraphs = []
            for paragraph in slide.iter(_A + "p"):
                text = "".join(
                    "\n" if node.tag == _A + "br" else (node.text or "")
                    for node in paragraph.iter()
                    if node.tag in (_A + "t", _A + "br")
                ).strip()
                if text:
                    paragraphs.append(text)
            slides.append("\n".join(paragraphs))
    return slides


def find_soffice() -> str | None:
    """The LibreOffice program, from PATH or its standard Windows and macOS locations."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return next((place for place in SOFFICE_LOCATIONS if Path(place).exists()), None)


def office_to_pdf(path: Path, out_dir: Path) -> Path:
    """Convert an old-format Word or PowerPoint file to PDF with LibreOffice."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("old Office format: install LibreOffice to read .doc and .ppt files")
    out_dir.mkdir(parents=True, exist_ok=True)
    # A private profile, so this never collides with a LibreOffice window the user has open.
    with tempfile.TemporaryDirectory() as profile:
        done = subprocess.run(
            [soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
            capture_output=True, timeout=600,
        )
    pdf = out_dir / (path.stem + ".pdf")
    if done.returncode != 0 or not pdf.exists():
        detail = done.stderr.decode("utf-8", "replace").strip()[:200]
        raise RuntimeError(f"LibreOffice could not convert the file. {detail}".strip())
    return pdf


def reader_name(path: Path, engine: str) -> str:
    """Which reader a file gets. Stored in the index, so a change of reader re-reads the file."""
    extension = path.suffix.lower().lstrip(".")
    if extension in ("docx", "pptx"):
        return extension
    return f"libreoffice+{engine}" if extension in LEGACY_OFFICE_EXTENSIONS else engine


def read_document(path: Path, engine: str) -> tuple[list[str], bool]:
    """Texts of a document's pages, slides or parts, and whether they are real page numbers."""
    extension = path.suffix.lower().lstrip(".")
    if extension == "docx":
        return docx_parts(path), False
    if extension == "pptx":
        return pptx_slides(path), True
    if extension in LEGACY_OFFICE_EXTENSIONS:
        return pdf_pages(office_to_pdf(path, CONVERTED_DIR), engine), True
    return pdf_pages(path, engine), True


def record_lines(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The text sections of one PSI record, as output lines."""
    psi_id = record["accession"]
    base = {"source_id": f"{psi_id}_record", "psi_id": psi_id, "category": "PSI record", "page": None}
    sections = {"title": record.get("title") or ""}
    sections.update({name: record.get(field) or "" for field, name in RECORD_SECTIONS.items()})
    publications = [
        ". ".join(part for part in (pub.get("authorList"), pub.get("title"), pub.get("doi")) if part)
        for pub in record.get("publications") or []
    ]
    sections["publications"] = "\n".join(publications)
    return [
        {**base, "section": name, "text": " ".join(text.split()) if name != "publications" else text}
        for name, text in sections.items()
        if text.strip()
    ]


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _summary(lines: list[dict[str, Any]], **more: Any) -> dict[str, Any]:
    chars = sum(len(line["text"]) for line in lines)
    return {"pages": len(lines), "chars": chars, **more}


def run(args: argparse.Namespace) -> int:
    manifest = fetch.load_manifest()
    documents = [
        f for f in manifest["files"]
        if f["category"] != "Experimental table"
        and f["file_name"].rsplit(".", 1)[-1].lower() in READABLE_EXTENSIONS
    ]
    if not documents and not fetch.psi_folders():
        print("extract-text: nothing in data/raw; run the inventory and fetch steps first.")
        return 1

    try:
        engine = pdf_engine()
    except RuntimeError as exc:
        print(f"extract-text: {exc}")
        return 1
    old = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    index: dict[str, dict[str, Any]] = {}
    read, reused, unreadable, scanned = 0, 0, [], []

    for entry in documents:
        source_id = entry["file_name"]
        path = config.RAW_DIR / entry["path"]
        out = TEXT_DIR / f"{path.name}.jsonl"  # the on-disk name is safe on every system
        reader = reader_name(path, engine)
        previous = old.get(source_id)
        unchanged = previous and (previous.get("sha256"), previous.get("engine")) == (entry["sha256"], reader)
        if unchanged and (out.exists() or "error" in previous):
            index[source_id] = previous
            reused += 1
        else:
            try:
                pages, paged = read_document(path, engine)
                if not pages:  # a corrupt PDF opens as zero pages; a Word file may hold only a picture
                    raise ValueError("the file has no readable pages or text")
            except Exception as exc:  # corrupt or password-protected file
                out.unlink(missing_ok=True)
                index[source_id] = {
                    "pages": 0, "chars": 0, "psi_id": entry["psi_id"], "category": entry["category"],
                    "sha256": entry["sha256"], "engine": reader,
                    "error": " ".join(f"{type(exc).__name__}: {exc}".split())[:200],
                }
                read += 1
                continue
            base = {"source_id": source_id, "psi_id": entry["psi_id"], "category": entry["category"]}
            lines = [
                {**base, "page": number, "text": text} if paged
                else {**base, "page": None, "section": f"part {number}", "text": text}
                for number, text in enumerate(pages, start=1)
            ]
            _write_jsonl(out, lines)
            index[source_id] = _summary(
                lines, psi_id=entry["psi_id"], category=entry["category"], sha256=entry["sha256"],
                engine=reader, empty_pages=sum(1 for text in pages if not text),
            )
            info = index[source_id]
            if not paged:
                info["paged"] = False
            info["scanned"] = bool(info["pages"]) and info["chars"] / info["pages"] < SCANNED_CHARS_PER_PAGE
            read += 1
    for source_id, info in index.items():
        if "error" in info:
            unreadable.append(f"{source_id}: {info['error']}")
        elif info.get("scanned"):
            scanned.append(source_id)

    records = 0
    for folder in fetch.psi_folders():
        record_path = folder / "record.json"
        if not record_path.exists():
            continue
        lines = record_lines(json.loads(record_path.read_text(encoding="utf-8")))
        source_id = f"{folder.name}_record"
        _write_jsonl(TEXT_DIR / f"{source_id}.jsonl", lines)
        index[source_id] = _summary(lines, psi_id=folder.name, category="PSI record")
        records += 1

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    pages = sum(i["pages"] for i in index.values() if i["category"] != "PSI record")
    chars = sum(i["chars"] for i in index.values())
    print(
        f"extract-text ({engine}): {read} documents read, {reused} unchanged, {len(unreadable)} unreadable, "
        f"{records} PSI records; {pages} pages, {chars / 1e6:.1f} million characters"
    )
    if scanned:
        print(f"extract-text: {len(scanned)} document(s) have almost no text (scans or image-only slides):")
        for source_id in scanned[:10]:
            print(f"  scanned: {source_id}")
        if len(scanned) > 10:
            print(f"  ... and {len(scanned) - 10} more; all are marked in index.json")
    for line in unreadable:
        print(f"  unreadable: {line}")
    return 0
