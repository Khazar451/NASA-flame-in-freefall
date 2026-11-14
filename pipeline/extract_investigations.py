"""Phase 4: build data/out/investigations.json, one record per PSI investigation.

Most fields are copied unchanged from the PSI record saved by the inventory step. Two
fields need a judgement and are proposed by the AI, then checked by the domain lead:
  role        prevention | detection | suppression | fundamentals
  fuel_phase  solid | liquid | gas | other
For each one the AI must quote the sentence it relied on, and the quote is checked
against the record text. Quotes that are not found word for word are flagged.

Three steps share this module:
  extract-investigations   AI tags -> pipeline/review/investigation_tags.json, then build
  review-export            write pipeline/review/investigations.csv for the domain lead
  apply-overrides          reviewed CSV -> data/overrides.json, then build again
Corrections in data/overrides.json are applied last on every build, so re-running the
pipeline never loses a human correction.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel, Field

from pipeline import config, fetch, inventory, llm, psi
from pipeline.models import FuelPhase, Investigation, InvestigationsFile, Role

# Committed, so a rebuild on another machine needs no AI calls (the free tier allows few).
TAGS = config.PIPELINE_DIR / "review" / "investigation_tags.json"
OVERRIDES = config.DATA_DIR / "overrides.json"
REVIEW_CSV = config.PIPELINE_DIR / "review" / "investigations.csv"
OUT = config.OUT_DIR / "investigations.json"

TAG_FIELDS = {"role": get_args(Role), "fuel_phase": get_args(FuelPhase)}
REVIEW_COLUMNS = [
    "psi_id", "acronym", "title", "field", "ai_value", "evidence", "evidence_found",
    "agree", "corrected_value", "reviewer_note",
]
REVIEWER_COLUMNS = ("agree", "corrected_value", "reviewer_note")
YES = {"y", "yes", "true", "1", "x"}

# PSI record field -> label used in the prompt
PROMPT_FIELDS = {
    "title": "Title",
    "investigationAcronym": "Acronym",
    "subResearchArea": "Sub-area",
    "experimentHardware": "Hardware",
    "objective": "Objectives",
    "approach": "Approach",
    "hypothesis": "Hypothesis",
    "researchImpacts": "Research impacts",
}

SYSTEM = """You tag NASA microgravity combustion investigations for a spacecraft fire-safety dashboard.
Use only the text you are given. Do not use outside knowledge about the experiment.

role: the single best fit for the investigation's main objective.
- prevention: how materials ignite, burn, spread flame or go out by themselves (material flammability,
  flame spread, flammability limits). This knowledge is used to choose safe materials.
- detection: smoke, aerosols, particles or other fire signatures, or instruments that detect them.
- suppression: putting flames out with an agent or a change of atmosphere (CO2, helium, nitrogen
  dilution, extinguishers).
- fundamentals: basic flame science (flame structure, soot, cool flames, electric fields, chemical
  kinetics) whose main aim is none of the three above.

fuel_phase: the phase of the fuel as it is supplied to the flame.
- solid: sheets, rods, fabrics, plastics, wires, smouldering materials.
- liquid: droplets or pools of liquid fuel.
- gas: gaseous fuel from a burner or jet, including a gas burner that imitates another fuel.
- other: no fuel is burned (for example dust or aerosol sampling).

For each of the two tags, copy one sentence or phrase from the text, word for word, that best
supports your choice. At most 300 characters. Never paraphrase or join separate passages."""

RETRY_NOTE = (
    "\n\nA previous answer quoted text that does not appear in the investigation text. "
    "Every evidence quote must be copied exactly, character for character."
)


class Tags(BaseModel):
    role: Role
    role_evidence: str = Field(min_length=1, max_length=400)
    fuel_phase: FuelPhase
    fuel_phase_evidence: str = Field(min_length=1, max_length=400)


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def record_text(record: dict[str, Any]) -> str:
    """The parts of a PSI record the AI reads, as one labelled text."""
    lines = [f"{label}: {clean(record.get(field))}" for field, label in PROMPT_FIELDS.items()]
    return "\n".join(line for line in lines if not line.endswith(": "))


def _normal(text: str) -> str:
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                                         "–": "-", "—": "-", " ": " "}))
    return " ".join(text.lower().split()).strip(" .\"'")


def quote_found(quote: str, source: str) -> bool:
    """True if the quote appears in the source, ignoring case, spacing and quote styles."""
    return bool(quote.strip()) and _normal(quote) in _normal(source)


def tag_record(
    record: dict[str, Any], provider: llm.Provider, use_cache: bool = True
) -> dict[str, Any]:
    """Ask the AI for the two tags of one investigation and check its quotes."""
    source = record_text(record)
    prompt = f"Investigation {record['accession']}\n\n{source}"
    result: dict[str, Any] = {}
    for attempt in range(2):
        reply = llm.complete_json(
            prompt + (RETRY_NOTE if attempt else ""), Tags, system=SYSTEM, provider=provider,
            use_cache=use_cache,
        )
        result = {
            "role": reply["role"],
            "role_evidence": clean(reply["role_evidence"]),
            "role_evidence_found": quote_found(reply["role_evidence"], source),
            "fuel_phase": reply["fuel_phase"],
            "fuel_phase_evidence": clean(reply["fuel_phase_evidence"]),
            "fuel_phase_evidence_found": quote_found(reply["fuel_phase_evidence"], source),
            "model": getattr(provider, "last_model", provider.model),
        }
        if result["role_evidence_found"] and result["fuel_phase_evidence_found"]:
            break
    return result


def load_records() -> list[dict[str, Any]]:
    return [
        json.loads((folder / "record.json").read_text(encoding="utf-8"))
        for folder in fetch.psi_folders()
        if (folder / "record.json").exists()
    ]


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def hardware_families() -> dict[str, str]:
    if not inventory.INVENTORY_CSV.exists():
        return {}
    with inventory.INVENTORY_CSV.open(newline="", encoding="utf-8") as fh:
        return {row["psi_id"]: row.get("hardware_family") or "" for row in csv.DictReader(fh)}


def investigation_from(record: dict[str, Any], tags: dict[str, Any], family: str) -> dict[str, Any]:
    """One contract record: PSI's own fields plus the two tags."""
    psi_id = record["accession"]
    return {
        "id": psi_id,
        "acronym": clean(record.get("investigationAcronym")) or None,
        "title": clean(record.get("title")),
        "project_type": "ground" if (record.get("projectType") or "").startswith("Ground") else "flight",
        "role": tags["role"],
        "fuel_phase": tags["fuel_phase"],
        "hardware": clean(record.get("experimentHardware")) or None,
        "related": re.findall(r"PSI-\d+", record.get("related_glds") or ""),
        "doi": clean(record.get("doi")) or None,
        "url": f"{psi.SITE_URL}/{psi_id}",
        "hardware_family": family or None,
        "sub_area": clean(record.get("subResearchArea")) or None,
        "objectives": clean(record.get("objective")) or None,
        "earth_benefits": clean(record.get("researchImpacts")) or None,
        "n_publications": len(record.get("publications") or []),
    }


def build() -> tuple[int, list[str]]:
    """Write investigations.json from records, AI tags and overrides. Return (count, problems)."""
    records = load_records()
    tags = _read_json(TAGS, {})
    overrides = _read_json(OVERRIDES, {})
    corrections: dict[str, dict[str, Any]] = overrides.get("investigations", {})
    families = hardware_families()
    problems: list[str] = []
    unverified: list[str] = []
    investigations = []
    for record in records:
        psi_id = record["accession"]
        if psi_id not in tags:
            problems.append(f"{psi_id}: no AI tags yet; run extract-investigations")
            continue
        row = investigation_from(record, tags[psi_id], families.get(psi_id, ""))
        row.update(corrections.get(psi_id, {}))
        for field in TAG_FIELDS:
            corrected = field in corrections.get(psi_id, {})
            if not tags[psi_id][f"{field}_evidence_found"] and not corrected:
                unverified.append(f"{psi_id}.{field}")
        investigations.append(Investigation.model_validate(row).model_dump(mode="json"))
    for psi_id in corrections:
        if psi_id not in {r["accession"] for r in records}:
            problems.append(f"overrides.json names {psi_id}, which is not an investigation")
    meta = {
        "source": "NASA Physical Sciences Informatics (psi.nasa.gov), research area Combustion Science",
        "roles_reviewed": bool(overrides.get("roles_reviewed")),
        "tags_corrected_by_reviewer": sum(len(c) for c in corrections.values()),
        "tags_with_unverified_evidence": unverified,
    }
    payload = InvestigationsFile(meta=meta, investigations=investigations).model_dump(mode="json")
    _write_json(OUT, payload)
    return len(investigations), problems


def _report_build() -> int:
    count, problems = build()
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    print(f"Wrote {count} investigations to {OUT.relative_to(config.REPO_ROOT)}")
    return 1 if problems else 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--retag", action="store_true", help="ask the AI again for every investigation")


def run(args: argparse.Namespace) -> int:
    records = load_records()
    if not records:
        print("extract-investigations: no records in data/raw; run the inventory step first.")
        return 1
    retag = getattr(args, "retag", False)
    tags = {} if retag else _read_json(TAGS, {})
    todo = [r for r in records if r["accession"] not in tags]
    if todo:
        try:
            provider = llm.get_provider()
        except llm.LLMError as exc:
            print(f"extract-investigations: {exc}")
            return 1
        for record in todo:
            for attempt in range(3):
                try:
                    # --retag also skips the on-disk AI cache, or it would return the old answer
                    tags[record["accession"]] = tag_record(record, provider, use_cache=not retag)
                    break
                except llm.LLMError as exc:
                    # A used-up quota will not recover in seconds; anything else may.
                    if attempt == 2 or isinstance(exc, llm.QuotaError):
                        _write_json(TAGS, tags)
                        print(f"extract-investigations: {record['accession']} failed: {exc}")
                        return 1
                    time.sleep(20 * (attempt + 1))
        _write_json(TAGS, tags)
    not_found = [
        f"{psi_id}.{field}" for psi_id, t in tags.items() for field in TAG_FIELDS
        if not t[f"{field}_evidence_found"]
    ]
    print(
        f"extract-investigations: {len(todo)} tagged by the AI, {len(records) - len(todo)} already tagged; "
        f"{len(not_found)} tag(s) whose quote was not found in the record"
    )
    for item in not_found:
        print(f"  quote not found: {item}")
    return _report_build()


def review_rows(records: list[dict[str, Any]], tags: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        psi_id = record["accession"]
        if psi_id not in tags:
            continue
        for field in TAG_FIELDS:
            rows.append({
                "psi_id": psi_id,
                "acronym": clean(record.get("investigationAcronym")),
                "title": clean(record.get("title")),
                "field": field,
                "ai_value": tags[psi_id][field],
                "evidence": tags[psi_id][f"{field}_evidence"],
                "evidence_found": "yes" if tags[psi_id][f"{field}_evidence_found"] else "NO",
                "agree": "", "corrected_value": "", "reviewer_note": "",
            })
    return rows


def read_review() -> list[dict[str, str]]:
    if not REVIEW_CSV.exists():
        return []
    with REVIEW_CSV.open(newline="", encoding="utf-8-sig") as fh:  # Excel adds a byte-order mark
        return list(csv.DictReader(fh))


def run_review_export(args: argparse.Namespace) -> int:
    tags = _read_json(TAGS, {})
    rows = review_rows(load_records(), tags)
    if not rows:
        print("review-export: no AI tags yet; run extract-investigations first.")
        return 1
    kept = {(r["psi_id"], r["field"]): r for r in read_review()}
    for row in rows:
        previous = kept.get((row["psi_id"], row["field"]))
        if previous:  # never lose what the reviewer typed
            row.update({col: previous.get(col) or "" for col in REVIEWER_COLUMNS})
    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    done = sum(1 for r in rows if r["agree"].strip().lower() in YES or r["corrected_value"].strip())
    print(f"review-export: {len(rows)} rows in {REVIEW_CSV.relative_to(config.REPO_ROOT)}, {done} reviewed")
    return 0


def overrides_from_review(rows: list[dict[str, str]]) -> tuple[dict[str, Any], list[str]]:
    """Turn the reviewed sheet into the overrides file. Return (overrides, problems)."""
    corrections: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    reviewed = 0
    for row in rows:
        psi_id, field = row.get("psi_id", ""), row.get("field", "")
        corrected = (row.get("corrected_value") or "").strip().lower()
        if field not in TAG_FIELDS:
            problems.append(f"{psi_id}: unknown field {field!r}")
            continue
        if corrected:
            if corrected not in TAG_FIELDS[field]:
                allowed = ", ".join(TAG_FIELDS[field])
                problems.append(f"{psi_id}.{field}: {corrected!r} is not one of {allowed}")
                continue
            if corrected != row.get("ai_value"):
                corrections.setdefault(psi_id, {})[field] = corrected
            reviewed += 1
        elif (row.get("agree") or "").strip().lower() in YES:
            reviewed += 1
    overrides = {
        "roles_reviewed": bool(rows) and reviewed == len(rows) and not problems,
        "investigations": dict(sorted(corrections.items(), key=lambda kv: inventory.psi_number(kv[0]))),
    }
    return overrides, problems


def run_apply_overrides(args: argparse.Namespace) -> int:
    rows = read_review()
    if not rows:
        print("apply-overrides: no review sheet yet; run review-export first.")
        return 1
    overrides, problems = overrides_from_review(rows)
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    if problems:
        print("apply-overrides: fix the review sheet; data/overrides.json was not changed.")
        return 1
    _write_json(OVERRIDES, overrides)
    changed = sum(len(c) for c in overrides["investigations"].values())
    print(
        f"apply-overrides: {changed} correction(s) in {OVERRIDES.relative_to(config.REPO_ROOT)}; "
        f"review {'complete' if overrides['roles_reviewed'] else 'not complete'}"
    )
    return _report_build()
