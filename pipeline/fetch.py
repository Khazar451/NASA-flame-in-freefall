"""Phase 3: download experimental tables and PDF documents from PSI.

Reads the file trees saved by the inventory step (data/raw/<psi_id>/files.json),
downloads the wanted files into
  data/raw/<psi_id>/tables/    experimental-table CSVs
  data/raw/<psi_id>/docs/      PDF, Word and PowerPoint files from the document categories
and records every file in data/raw/manifest.json (name, version, size, sha256, date).
Files PSI lists but cannot serve (HTTP 404) are recorded under "unavailable" and do not
fail the run; any other download error does.

Raw Data is never downloaded: several investigations hold about 2 TB of video each.
Safe to re-run: a file already on disk with the listed size is not fetched again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import config, psi

MANIFEST = config.RAW_DIR / "manifest.json"

# category -> (file extensions to download, subfolder under data/raw/<psi_id>/)
DOCUMENT_TYPES = {"pdf", "doc", "docx", "ppt", "pptx"}
WANTED: dict[str, tuple[set[str], str]] = {
    "Experimental table": ({"csv"}, "tables"),
    "Reports": (DOCUMENT_TYPES, "docs"),
    "Science Documents": (DOCUMENT_TYPES, "docs"),
    "Engineering Documents": (DOCUMENT_TYPES, "docs"),
    "Presentations": (DOCUMENT_TYPES, "docs"),
}
MAX_BYTES = 60 * 1024 * 1024  # second guard; the largest wanted PDF is about 49 MB

# PSI file names include ":" and "/" and run to 240 characters; Windows accepts neither
# those characters nor paths much beyond 260.
FORBIDDEN_IN_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_NAME_LENGTH = 150


@dataclass(frozen=True)
class Item:
    psi_id: str
    category: str
    file_name: str  # as PSI names it; a few names contain "/"
    version: int
    bytes: int  # size on disk; PSI's listing is occasionally out of date
    path: str  # relative to data/raw/


def local_name(file_name: str) -> str:
    """A name for the file on disk that Windows, macOS and Linux all accept."""
    name = FORBIDDEN_IN_NAMES.sub("_", file_name).strip()
    if len(name) <= MAX_NAME_LENGTH:
        return name
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    # A hash of the full name keeps two long names with the same beginning apart.
    tag = "~" + hashlib.sha1(file_name.encode("utf-8")).hexdigest()[:8]
    stem = stem[: MAX_NAME_LENGTH - len(tag) - len(dot) - len(extension)].rstrip(" .")
    return f"{stem}{tag}{dot}{extension}"


def select(psi_id: str, tree: dict[str, Any]) -> tuple[list[Item], Counter[str]]:
    """Split one investigation's file tree into files to download and reasons for skipping."""
    items: list[Item] = []
    skipped: Counter[str] = Counter()
    for category, node in psi.iter_files(tree):
        if category not in WANTED:
            skipped[f"category not wanted: {category}"] += 1
            continue
        extensions, subfolder = WANTED[category]
        # key = psi-data/<psi_id>/<category folder>/<file name>
        file_name = node["key"].split("/", 3)[-1]
        extension = file_name.rsplit(".", 1)[-1].lower()
        size = int(node.get("bytes_size") or 0)
        if extension not in extensions:
            skipped[f"file type not wanted: .{extension}"] += 1
        elif size > MAX_BYTES:
            skipped["over the size limit"] += 1
        elif node.get("restricted") or node.get("restrictedAccess"):
            skipped["restricted"] += 1
        else:
            items.append(
                Item(psi_id, category, file_name, int(node.get("version") or 1), size,
                     f"{psi_id}/{subfolder}/{local_name(file_name)}")
            )
    return items, skipped


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_one(item: Item, known: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """Return (manifest entry, downloaded?) for one file, fetching it only if needed."""
    dest = config.RAW_DIR / item.path
    previous = known.get(item.path)
    on_disk = dest.stat().st_size if dest.exists() else None
    if previous and previous.get("version") == item.version and previous.get("bytes") == on_disk:
        return previous, False
    if on_disk is not None and on_disk == item.bytes:  # downloaded by an interrupted run
        return {**asdict(item), "sha256": _sha256(dest), "fetched_at": _now()}, False
    # psi.download checks the size against the server's Content-Length, so a size that
    # differs from PSI's listing is a stale listing, not a broken download.
    size, sha = psi.download(item.psi_id, item.file_name, item.version, dest)
    entry = {**asdict(item), "bytes": size, "sha256": sha, "fetched_at": _now()}
    if size != item.bytes:
        entry["listed_bytes"] = item.bytes
    return entry, True


def load_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {"files": [], "unavailable": [], "skipped": {}}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def psi_folders() -> list[Path]:
    folders = [p for p in config.RAW_DIR.glob("PSI-*") if (p / "files.json").exists()]
    return sorted(folders, key=lambda p: int(p.name.split("-")[1]))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="list what would be downloaded")


def run(args: argparse.Namespace) -> int:
    folders = psi_folders()
    if not folders:
        print("fetch: no saved file trees in data/raw; run the inventory step first.")
        return 1

    items: list[Item] = []
    skipped: Counter[str] = Counter()
    for folder in folders:
        tree = json.loads((folder / "files.json").read_text(encoding="utf-8"))
        wanted, why = select(folder.name, tree)
        items += wanted
        skipped += why

    total_mb = sum(i.bytes for i in items) / 1e6
    print(f"fetch: {len(items)} files wanted ({total_mb:.0f} MB) from {len(folders)} investigations")
    if getattr(args, "dry_run", False):
        for reason, n in sorted(skipped.items()):
            print(f"  skipped {n:>6}  {reason}")
        return 0

    known = {entry["path"]: entry for entry in load_manifest()["files"]}
    entries: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    failures: list[str] = []
    downloaded = 0

    def work(item: Item) -> tuple[Item, dict[str, Any] | None, bool, Exception | None]:
        try:
            entry, fresh = fetch_one(item, known)
            return item, entry, fresh, None
        except psi.PSIError as exc:
            return item, None, False, exc

    with ThreadPoolExecutor(max_workers=6) as pool:
        for item, entry, fresh, error in pool.map(work, items):
            if isinstance(error, psi.PSINotFound):
                unavailable.append({**asdict(item), "reason": "listed by PSI but not served (HTTP 404)"})
            elif error is not None:
                failures.append(f"{item.path}: {error}")
            elif entry is not None:
                entries.append(entry)
                downloaded += fresh

    def order(entry: dict[str, Any]) -> tuple[int, str]:
        return int(entry["psi_id"].split("-")[1]), entry["path"]

    manifest = {
        "files": sorted(entries, key=order),
        "unavailable": sorted(unavailable, key=order),
        "skipped": dict(sorted(skipped.items())),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(
        f"fetch: {downloaded} downloaded, {len(entries) - downloaded} already on disk, "
        f"{len(unavailable)} unavailable at PSI, {len(failures)} failed"
    )
    for entry in manifest["unavailable"]:
        print(f"  unavailable: {entry['path']}")
    for failure in failures[:20]:
        print(f"  FAILED: {failure}")
    return 1 if failures else 0
