"""Phase 6: build data/out/coverage.json, the grid behind the gap map.

Every test with both oxygen and pressure falls into one cell of
  fuel phase x oxygen band x pressure band
and each cell counts its tests. All cells are written, including empty ones: an empty
cell is a combination of fuel and atmosphere that no PSI test has covered.

The bands are chosen so the habitat atmospheres in app/src/habitats.ts line up with
band edges: the lunar range (30 % oxygen or more, 4.3 to 10.2 psi) is exactly the
cells in the two top oxygen bands and the second pressure band. Every PSI test is a
microgravity test, so gravity is not a grid dimension; meta says so.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from typing import Any

from pipeline import config, transform_tests
from pipeline.models import Band, CoverageFile

OUT = config.OUT_DIR / "coverage.json"
KPA_PER_PSI = 6.894757
SUIT_KPA = round(4.3 * KPA_PER_PSI, 2)    # 29.65 kPa, also the lunar range's lower edge
LUNAR_MAX_KPA = round(10.2 * KPA_PER_PSI, 2)  # 70.33 kPa

O2_BANDS = [
    Band(id="o2_below_air", label="Below air (under 19 %)", min=0, max=19, habitats=[]),
    Band(id="o2_air", label="Air-like (19 to 23.5 %)", min=19, max=23.5, habitats=["cabin"]),
    Band(id="o2_enriched", label="Enriched (23.5 to 30 %)", min=23.5, max=30, habitats=[]),
    Band(id="o2_lunar", label="Lunar-habitat range (30 to 50 %)", min=30, max=50, habitats=["lunar"]),
    Band(id="o2_high", label="High (50 % and over)", min=50, max=None, habitats=["lunar", "suit"]),
]
PRESSURE_BANDS = [
    Band(id="p_below_suit", label=f"Below spacesuit pressure (under {SUIT_KPA:g} kPa)",
         min=0, max=SUIT_KPA, habitats=[]),
    Band(id="p_lunar", label=f"Spacesuit to lunar-habitat pressure ({SUIT_KPA:g} to {LUNAR_MAX_KPA:g} kPa)",
         min=SUIT_KPA, max=LUNAR_MAX_KPA, habitats=["lunar", "suit"]),
    Band(id="p_reduced", label=f"Reduced ({LUNAR_MAX_KPA:g} to 95 kPa)", min=LUNAR_MAX_KPA, max=95, habitats=[]),
    Band(id="p_cabin", label="Sea level and ISS cabin (95 to 110 kPa)", min=95, max=110, habitats=["cabin"]),
    Band(id="p_elevated", label="Elevated (110 kPa and over)", min=110, max=None, habitats=[]),
]
FUEL_PHASES = ("solid", "liquid", "gas")
ORDER = {"cabin": 0, "lunar": 1, "suit": 2}


def band_of(value: float, bands: list[Band]) -> str:
    for band in bands:
        if value >= band.min and (band.max is None or value < band.max):
            return band.id
    raise ValueError(f"{value} is below every band")


def psi_order(psi_id: str) -> int:
    return int(psi_id.split("-")[1])


def build(tests: list[dict[str, Any]], assumed: list[tuple[str, str, set[str]]]) -> dict[str, Any]:
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for phase in FUEL_PHASES:
        for o2 in O2_BANDS:
            for p in PRESSURE_BANDS:
                cells[(phase, o2.id, p.id)] = {
                    "fuel_phase": phase, "o2_band": o2.id, "pressure_band": p.id,
                    "n_tests": 0, "n_pressure_assumed": 0, "investigations": set(),
                    "outcomes": Counter(),
                    "habitats": sorted(set(o2.habitats) & set(p.habitats), key=ORDER.get),
                }
    off: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"n_tests": 0, "investigations": set()})
    for row in tests:
        phase = row["fuel_phase"]
        reason = None
        if phase not in FUEL_PHASES:
            reason = "fuel phase other than solid, liquid or gas"
        elif "oxidizer_o2_pct" in (row.get("extra") or {}):
            reason = "inverse flame: oxygen comes from the burner, no ambient oxygen level"
        elif row["o2_pct"] is None and row["pressure_kpa"] is None:
            reason = "source table gives neither oxygen nor pressure"
        elif row["o2_pct"] is None:
            reason = "source table gives no oxygen"
        elif row["pressure_kpa"] is None:
            reason = "source table gives no pressure"
        if reason:
            entry = off[(phase, reason)]
            entry["n_tests"] += 1
            entry["investigations"].add(row["investigation_id"])
            continue
        cell = cells[(phase, band_of(row["o2_pct"], O2_BANDS), band_of(row["pressure_kpa"], PRESSURE_BANDS))]
        cell["n_tests"] += 1
        cell["n_pressure_assumed"] += "pressure_kpa" in transform_tests.assumed_in(row, assumed)
        cell["investigations"].add(row["investigation_id"])
        cell["outcomes"][row["outcome"]] += 1

    out_cells = [
        {**c, "investigations": sorted(c["investigations"], key=psi_order), "outcomes": dict(c["outcomes"])}
        for c in cells.values()
    ]
    not_on_grid = [
        {"fuel_phase": phase, "reason": reason, "n_tests": e["n_tests"],
         "investigations": sorted(e["investigations"], key=psi_order)}
        for (phase, reason), e in sorted(off.items())
    ]
    on_grid = sum(c["n_tests"] for c in out_cells)
    meta = {
        "source": "data/out/tests.json",
        "gravity": "All PSI combustion tests ran in microgravity. No partial-gravity (Moon or Mars) tests exist in PSI.",
        "tests_on_grid": on_grid,
        "tests_not_on_grid": sum(e["n_tests"] for e in not_on_grid),
        "empty_cells": sum(c["n_tests"] == 0 for c in out_cells),
        "cells": len(out_cells),
    }
    payload = {"meta": meta, "o2_bands": [b.model_dump() for b in O2_BANDS],
               "pressure_bands": [b.model_dump() for b in PRESSURE_BANDS],
               "cells": out_cells, "not_on_grid": not_on_grid}
    return CoverageFile.model_validate(payload).model_dump(mode="json")


def habitat_summary(data: dict[str, Any]) -> list[str]:
    """One line per habitat and fuel phase: how many tests fall inside that habitat's cells."""
    lines = []
    for habitat in ("cabin", "lunar", "suit"):
        counts = {
            phase: sum(c["n_tests"] for c in data["cells"] if habitat in c["habitats"] and c["fuel_phase"] == phase)
            for phase in FUEL_PHASES
        }
        lines.append(f"{habitat:6} " + ", ".join(f"{phase} {n}" for phase, n in counts.items()))
    return lines


def run(args: argparse.Namespace) -> int:
    tests_path = config.OUT_DIR / "tests.json"
    if not tests_path.exists():
        print("coverage: data/out/tests.json not found; run transform-tests first.")
        return 1
    tests = json.loads(tests_path.read_text(encoding="utf-8"))["tests"]
    data = build(tests, transform_tests.assumed_fields())
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    meta = data["meta"]
    print(
        f"coverage: {meta['tests_on_grid']} tests on the grid, {meta['tests_not_on_grid']} not; "
        f"{meta['empty_cells']} of {meta['cells']} cells are empty"
    )
    print("coverage: tests inside each habitat's cells:")
    for line in habitat_summary(data):
        print(f"  {line}")
    print(f"Wrote {OUT.relative_to(config.REPO_ROOT)}")
    return 0
