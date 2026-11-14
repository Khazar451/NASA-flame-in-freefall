"""Phase 8: build data/out/gravity.json, the data behind a partial-gravity panel.

PSI has no combustion tests in partial gravity (Moon or Mars). What it does have, and
what this file gathers, all built by plain code with every quote copied word for word:
  * levels       microgravity (every PSI test), Moon and Mars (no tests), Earth
                 (comparison only), with test counts from tests.json
  * stand_in     ACME E-FIELD, whose electric fields drive a flow that can stand in for
                 buoyancy, with quotes from its PSI record and documents
  * paired       Saffire-II, the one test table that reports normal-gravity (1 g) burn
                 results beside the microgravity results for the same samples
  * statements   what each PSI record says about gravity
  * upcoming     LUCI and FM2, named in the challenge brief (not in PSI yet)
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path
from typing import Any

from pipeline import build_index, config, fetch, transform_tests
from pipeline.models import GravityFile

OUT = config.OUT_DIR / "gravity.json"
BRIEF_URL = "https://www.spaceappschallenge.org/2026/challenges/flame-in-freefall/?tab=details"
STAND_IN = "PSI-22"
PAIRED = ("PSI-99", "SAFFIRE-2.csv")
RECORD_FIELDS = {"objective": "objectives", "approach": "approach", "hypothesis": "hypothesis",
                 "researchImpacts": "Earth benefits"}
# Sentences that compare gravity levels, not every sentence that says "microgravity".
GRAVITY_SENTENCE = re.compile(
    r"normal[- ]gravity|reduced[- ]gravity|low[- ]g(?:ravity)?\b|\b1[- ]g\b|partial[- ]gravity|"
    r"NASA-STD-6001|(?:influence|effects?) of gravity|buoyancy forces",
    re.IGNORECASE,
)
NOT_PROSE = re.compile(r"doi\.org|\bet al\b|https?://", re.IGNORECASE)
# A reference the E-FIELD team cites: a study that used electric fields to simulate gravity.
STAND_IN_DOC = re.compile(r"Karnani, [A-Z]\. et al\., Simulating Gravity in Microgravity Combustion Using "
                          r"Electric Fields, CST, 2012")
STAND_IN_RECORD = re.compile(r"buoyancy forces|driving ion", re.IGNORECASE)
PER_INVESTIGATION = 2
SUMMARY = (
    "PSI has no combustion tests in partial gravity. Every PSI combustion test ran in microgravity, on the ISS "
    "or in a Cygnus cargo vehicle, and normal gravity on Earth is the other bound, so flame behaviour on the "
    "Moon (about 0.17 g) or Mars (about 0.38 g) lies between the two. The closest PSI data are ACME E-FIELD, "
    "where an electric field drives a flow that can stand in for buoyancy, and Saffire-II, which reports "
    "normal-gravity burn results beside its microgravity results for the same samples. The challenge brief "
    "names two planned experiments, LUCI and FM2, that are expected to add partial-gravity data."
)


def sentences(text: str | None) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if s]


def record_quotes(record: dict[str, Any], url: str, pattern: re.Pattern[str], limit: int) -> list[dict[str, Any]]:
    quotes = []
    for field, label in RECORD_FIELDS.items():
        for sentence in sentences(record.get(field)):
            if pattern.search(sentence) and not NOT_PROSE.search(sentence) and 40 <= len(sentence) <= 400:
                quotes.append({"investigation_id": record["accession"], "text": sentence,
                               "source_label": f"PSI record, {label}", "url": url})
                if len(quotes) == limit:
                    return quotes
    return quotes


def document_quote(chunks: list[dict[str, Any]], psi_id: str, pattern: re.Pattern[str], url: str) -> dict[str, Any] | None:
    """The first text matching `pattern` in an investigation's documents, as a quote."""
    for chunk in chunks:
        if chunk["psi_id"] != psi_id:
            continue
        match = pattern.search(chunk["text"])
        if not match:
            continue
        title = Path(chunk["source_id"]).stem
        return {"investigation_id": psi_id, "text": match.group(0),
                "source_label": f"{title}, p. {chunk['page']}" if chunk.get("page") else title, "url": url}
    return None


def paired_results(tests: list[dict[str, Any]], psi_id: str, table: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows = []
    for t in tests:
        if t["investigation_id"] != psi_id:
            continue
        extra = t.get("extra") or {}
        rows.append({
            "investigation_id": psi_id, "sample": t["test_label"], "material": t["fuel_material"],
            "flow_direction": extra.get("flow_direction"),
            "microgravity_burn_length": extra.get("microgravity_burn_length"),
            "microgravity_spread_rate": extra.get("microgravity_spread_rate"),
            "normal_gravity_burn_length": extra.get("normal_gravity_burn_length"),
            "normal_gravity_spread_rate": extra.get("normal_gravity_spread_rate"),
            "source": t["source"],
        })
    _, raw_rows = transform_tests.read_table(transform_tests.find_table(psi_id, table))
    notes = [row[0] for row in raw_rows if row and re.match(r"Note \d", row[0])]
    return rows, notes


NONE = re.compile(r"^~\s*0\b")  # "~ 0": no burn to speak of


def paired_summary(rows: list[dict[str, Any]]) -> str:
    """What the table shows, counted by plain code: samples that barely burned in microgravity but burned at 1 g."""
    def burned(length: str | None) -> bool:
        return bool(length) and not NONE.match(length) and "nomex" not in length.lower()

    flat = [r for r in rows if r["microgravity_burn_length"] and NONE.match(r["microgravity_burn_length"])]
    burned_at_1g = [r for r in flat if burned(r["normal_gravity_burn_length"])]
    if not flat:
        return ""
    materials = sorted({r["material"] for r in flat if r["material"]})
    return (
        f"In Saffire-II, {len(flat)} of {len(rows)} samples ({', '.join(materials)}) barely burned in microgravity "
        f"(burn length about 0), but {len(burned_at_1g)} of those {len(flat)} burned at normal gravity "
        f"({', '.join(r['normal_gravity_burn_length'] for r in burned_at_1g)}). The table does not say how the "
        "normal-gravity tests were run."
    )


def build(investigations: list[dict[str, Any]], tests: list[dict[str, Any]], records: dict[str, dict[str, Any]],
          chunks: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {i["id"]: i for i in investigations}
    flight = [i["id"] for i in investigations if i["project_type"] == "flight"]
    with_tests = sorted({t["investigation_id"] for t in tests}, key=lambda i: int(i.split("-")[1]))
    paired, notes = paired_results(tests, *PAIRED)
    levels = [
        {"id": "microgravity", "label": "Microgravity (ISS and Cygnus)", "g": 0.0, "status": "tested",
         "n_tests": len(tests), "investigations": flight,
         "note": "Every flight investigation in PSI ran here; test rows exist for those listed in tests.json."},
        {"id": "moon", "label": "Moon", "g": 0.17, "status": "no_data", "n_tests": 0, "investigations": [],
         "note": "No PSI combustion test has run at lunar gravity."},
        {"id": "mars", "label": "Mars", "g": 0.38, "status": "no_data", "n_tests": 0, "investigations": [],
         "note": "No PSI combustion test has run at Martian gravity."},
        {"id": "earth", "label": "Earth (normal gravity)", "g": 1.0, "status": "comparison_only", "n_tests": 0,
         "investigations": [PAIRED[0]],
         "note": f"PSI test tables hold flight results only. Saffire-II reports normal-gravity burn results "
                 f"beside its microgravity results for {len(paired)} samples; several investigations compare "
                 "against NASA's normal-gravity flammability test (NASA-STD-6001 Test 1)."},
    ]
    stand_in_url = by_id[STAND_IN]["url"]
    evidence = record_quotes(records[STAND_IN], stand_in_url, STAND_IN_RECORD, 2)
    doc = document_quote(chunks, STAND_IN, STAND_IN_DOC, stand_in_url)
    if doc:
        evidence.append(doc)
    statements = []
    for psi_id in sorted(records, key=lambda i: int(i.split("-")[1])):
        if psi_id in by_id:
            statements += record_quotes(records[psi_id], by_id[psi_id]["url"], GRAVITY_SENTENCE, PER_INVESTIGATION)
    payload = {
        "meta": {"source": "data/out/tests.json, PSI records, the search index and the challenge brief",
                 "investigations_with_test_rows": with_tests},
        "summary": SUMMARY,
        "levels": levels,
        "stand_in": {"investigation_id": STAND_IN, "evidence": evidence,
                     "summary": "ACME E-FIELD applied electric fields to flames in microgravity. The ions in the "
                                "flame drive a flow that can stand in for buoyancy, so its results are the "
                                "closest PSI data to partial gravity."},
        "paired_results": paired,
        "paired_summary": paired_summary(paired),
        "paired_notes": notes,
        "statements": statements,
        "upcoming": [
            {"name": name, "note": "Named in the challenge brief as a planned experiment that will add "
                                   "partial-gravity combustion data. Not in PSI yet.",
             "source_label": "Challenge brief", "url": BRIEF_URL}
            for name in ("LUCI", "FM2")
        ],
    }
    return GravityFile.model_validate(payload).model_dump(mode="json")


def run(args: argparse.Namespace) -> int:
    needed = [config.OUT_DIR / "investigations.json", config.OUT_DIR / "tests.json",
              build_index.SEARCH_DIR / build_index.CHUNKS]
    if not all(p.exists() for p in needed):
        print("gravity: investigations.json, tests.json and the search index are needed; run the earlier steps first.")
        return 1
    investigations = json.loads(needed[0].read_text(encoding="utf-8"))["investigations"]
    tests = json.loads(needed[1].read_text(encoding="utf-8"))["tests"]
    records = {f.name: json.loads((f / "record.json").read_text(encoding="utf-8"))
               for f in fetch.psi_folders() if (f / "record.json").exists()}
    with gzip.open(needed[2], "rt", encoding="utf-8") as fh:
        chunks = [json.loads(line) for line in fh.read().split("\n") if line]
    data = build(investigations, tests, records, chunks)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"gravity: {len(data['paired_results'])} paired Saffire-II results, {len(data['stand_in']['evidence'])} "
          f"E-FIELD quotes, {len(data['statements'])} record statements")
    print(f"Wrote {OUT.relative_to(config.REPO_ROOT)}")
    return 0
