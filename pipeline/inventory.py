"""Phase 1: build pipeline/inventory.csv from the PSI API.

For every combustion investigation this saves two files under data/raw/<psi_id>/
  record.json   the investigation record (objectives, hardware, publications, ...)
  files.json    the full file tree with sizes
and writes one row per investigation to pipeline/inventory.csv.

`hardware_family` starts from the families named in the challenge brief and `notes`
starts empty. Both are meant to be edited by hand in the CSV; a re-run keeps the edits.
Saved API replies are reused unless --refresh is given.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pipeline import config, psi

INVENTORY_CSV = config.PIPELINE_DIR / "inventory.csv"
RESEARCH_AREA = "Combustion Science"

COLUMNS = [
    "psi_id", "acronym", "title", "project_type", "platform", "hardware", "sub_area",
    "doi", "related", "version", "n_tables", "table_files", "n_docs", "docs_mb",
    "n_publications", "total_gb", "file_categories", "hardware_family", "notes", "url",
]
MANUAL_COLUMNS = ("hardware_family", "notes")

# Categories worth downloading in Phase 3, and the file types we can read text from.
DOC_CATEGORIES = {"Reports", "Science Documents", "Engineering Documents", "Presentations"}
DOC_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "txt"}

# First match wins, so CFI-G (an ACME experiment) is listed before CFI (droplets).
FAMILY_BY_ACRONYM = [
    ("ACME", "acme_gas_flames"),
    ("FLEX", "mdca_droplets"),
    ("CFI", "mdca_droplets"),
    ("BASS", "msg_flow_duct"),
    ("SPICE", "msg_flow_duct"),
    ("SLICE", "msg_flow_duct"),
    ("SAFFIRE", "saffire"),
    ("SAME", "smoke_aerosol"),
    ("DAFT", "smoke_aerosol"),
]


def psi_number(psi_id: str) -> int:
    return int(psi_id.split("-")[1])


def default_family(acronym: str, project_type: str) -> str:
    if project_type.startswith("Ground"):
        return "ground_study"
    upper = acronym.upper()
    for needle, family in FAMILY_BY_ACRONYM:
        if needle in upper:
            return family
    return ""


def summarize(record: dict[str, Any], tree: dict[str, Any]) -> dict[str, Any]:
    """One inventory row from an investigation record and its file tree."""
    psi_id = record["accession"]
    categories: Counter[str] = Counter()
    n_docs, doc_bytes, total_bytes = 0, 0, 0
    for category, node in psi.iter_files(tree):
        size = int(node.get("bytes_size") or 0)
        categories[category] += 1
        total_bytes += size
        extension = node.get("key", "").rsplit(".", 1)[-1].lower()
        if category in DOC_CATEGORIES and extension in DOC_EXTENSIONS:
            n_docs += 1
            doc_bytes += size

    tables = [t["file_name"] for t in record.get("experimentalTable") or []]
    acronym = (record.get("investigationAcronym") or "").strip()
    project_type = (record.get("projectType") or "").strip()
    return {
        "psi_id": psi_id,
        "acronym": acronym,
        "title": " ".join((record.get("title") or "").split()),
        "project_type": project_type,
        "platform": (record.get("flightProgram") or "").strip(),
        "hardware": " ".join((record.get("experimentHardware") or "").split()),
        "sub_area": (record.get("subResearchArea") or "").strip(),
        "doi": (record.get("doi") or "").strip(),
        "related": " ".join((record.get("related_glds") or "").split()),
        "version": record.get("version") or "",
        "n_tables": len(tables),
        "table_files": "; ".join(tables),
        "n_docs": n_docs,
        "docs_mb": round(doc_bytes / 1e6, 1),
        "n_publications": len(record.get("publications") or []),
        "total_gb": round(total_bytes / 1e9, 2),
        "file_categories": "; ".join(f"{name}: {n}" for name, n in sorted(categories.items())),
        "hardware_family": default_family(acronym, project_type),
        "notes": "",
        "url": f"{psi.SITE_URL}/{psi_id}",
    }


def read_manual_columns(path: Path) -> dict[str, dict[str, str]]:
    """Hand-edited values from an existing inventory.csv, keyed by psi_id."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            row["psi_id"]: {col: row.get(col) or "" for col in MANUAL_COLUMNS}
            for row in csv.DictReader(fh)
        }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    manual = read_manual_columns(path)
    for row in rows:
        for col, value in manual.get(row["psi_id"], {}).items():
            if value:  # an empty cell falls back to the default
                row[col] = value
    rows = sorted(rows, key=lambda r: psi_number(r["psi_id"]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _cached(path: Path, load, refresh: bool) -> dict[str, Any]:
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    data = load()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return data


def load_investigation(psi_id: str, refresh: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Record and file tree for one investigation, from data/raw/ or the API."""
    folder = config.RAW_DIR / psi_id
    record = _cached(folder / "record.json", lambda: psi.get_record(psi_id), refresh)
    tree = _cached(
        folder / "files.json", lambda: psi.get_file_tree(psi_id, record["version"]), refresh
    )
    return record, tree


def combustion_ids() -> list[str]:
    ids = [
        s["Accession"] for s in psi.search_investigations() if s.get("Research Area") == RESEARCH_AREA
    ]
    return sorted(ids, key=psi_number)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--refresh", action="store_true", help="re-download records already saved in data/raw/"
    )


def run(args: argparse.Namespace) -> int:
    refresh = getattr(args, "refresh", False)
    try:
        ids = combustion_ids()
        print(f"{len(ids)} combustion investigations: {', '.join(ids)}")
        with ThreadPoolExecutor(max_workers=6) as pool:
            loaded = list(pool.map(lambda i: load_investigation(i, refresh), ids))
    except psi.PSIError as exc:
        print(f"FAILED: {exc}")
        return 1
    rows = [summarize(record, tree) for record, tree in loaded]
    write_csv(rows, INVENTORY_CSV)
    print(f"Wrote {len(rows)} rows to {INVENTORY_CSV.relative_to(config.REPO_ROOT)}")
    return 0
