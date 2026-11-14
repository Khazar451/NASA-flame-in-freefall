"""Check investigations.json and tests.json against the contract and sanity ranges.

    python -m pipeline.run_all validate              checks data/out/
    python -m pipeline.run_all validate <folder>     checks another folder

Errors fail the run. Warnings are printed and do not.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from pipeline import config
from pipeline.models import CoverageFile, GravityFile, InvestigationsFile, TestsFile, VariedFile

# Hard limits: anything outside is a unit or mapping mistake, not a real test.
O2_PCT = (0.0, 100.0)
PRESSURE_KPA = (0.0, 1000.0)  # the highest-pressure PSI tests are a few atmospheres
FLOW_CM_S = (0.0, 1000.0)

MAX_SHOWN = 20


def _load(path: Path, model: type[BaseModel], errors: list[str]) -> Any:
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: not valid JSON ({exc})")
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(str(part) for part in err["loc"])
            errors.append(f"{path.name}: {where}: {err['msg']}")
    return None


def _duplicates(ids: list[str]) -> list[str]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def _outside(value: float | None, limits: tuple[float, float], open_low: bool = False) -> bool:
    if value is None:
        return False
    low, high = limits
    return value > high or value < low or (open_low and value == low)


def check(folder: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the two contract files in `folder`."""
    errors: list[str] = []
    warnings: list[str] = []
    inv_path, tests_path = folder / "investigations.json", folder / "tests.json"
    if not inv_path.exists():
        errors.append(f"{inv_path.name}: file not found in {folder}")
        if not tests_path.exists():
            errors.append(f"{tests_path.name}: file not found in {folder}")
        return errors, warnings

    inv_file = _load(inv_path, InvestigationsFile, errors)
    if tests_path.exists():
        tests_file = _load(tests_path, TestsFile, errors)
    else:  # investigations are built a phase before the tests
        warnings.append("tests.json is not built yet; only investigations.json was checked")
        tests_file = TestsFile(tests=[])
    if inv_file is None or tests_file is None:
        return errors, warnings

    investigations, tests = inv_file.investigations, tests_file.tests
    known = {inv.id for inv in investigations}

    for dup in _duplicates([inv.id for inv in investigations]):
        errors.append(f"investigations.json: id {dup} appears more than once")
    for inv in investigations:
        if inv.doi and inv.doi.lower().startswith(("http", "doi:")):
            errors.append(f"{inv.id}: doi must be the bare identifier, got {inv.doi!r}")
        if not inv.url.startswith("https://"):
            errors.append(f"{inv.id}: url must start with https://")
        for other in inv.related:
            if other not in known:
                warnings.append(f"{inv.id}: related investigation {other} is not in the file")

    for dup in _duplicates([t.id for t in tests]):
        errors.append(f"tests.json: id {dup} appears more than once")
    for t in tests:
        if t.investigation_id not in known:
            errors.append(f"{t.id}: investigation_id {t.investigation_id} is not in investigations.json")
        if _outside(t.o2_pct, O2_PCT):
            errors.append(f"{t.id}: o2_pct {t.o2_pct} is outside 0 to 100")
        if _outside(t.pressure_kpa, PRESSURE_KPA, open_low=True):
            errors.append(f"{t.id}: pressure_kpa {t.pressure_kpa} is outside 0 to {PRESSURE_KPA[1]:g}")
        if _outside(t.flow_cm_s, FLOW_CM_S):
            errors.append(f"{t.id}: flow_cm_s {t.flow_cm_s} is outside 0 to {FLOW_CM_S[1]:g}")
        if not t.sample and t.source is None:
            errors.append(f"{t.id}: a real row (sample=false) must record its source file and row")

    with_tests = {t.investigation_id for t in tests}
    without = sorted(known - with_tests, key=lambda i: int(i.split("-")[1]))
    if without and tests:
        warnings.append(f"{len(without)} investigation(s) have no test rows: {', '.join(without)}")
    n_sample = sum(t.sample for t in tests)
    if n_sample:
        warnings.append(f"{n_sample} of {len(tests)} test rows are placeholders (sample=true)")

    # The Phase 6 files are optional; when present they must agree with tests.json.
    coverage_path, varied_path = folder / "coverage.json", folder / "varied.json"
    if coverage_path.exists():
        coverage = _load(coverage_path, CoverageFile, errors)
        if coverage is not None:
            counted = sum(c.n_tests for c in coverage.cells) + sum(o.n_tests for o in coverage.not_on_grid)
            if counted != len(tests):
                errors.append(f"coverage.json counts {counted} tests but tests.json has {len(tests)}; rebuild it")
    if varied_path.exists():
        varied_file = _load(varied_path, VariedFile, errors)
        if varied_file is not None:
            per_inv = {}
            for t in tests:
                per_inv[t.investigation_id] = per_inv.get(t.investigation_id, 0) + 1
            for entry in varied_file.investigations:
                if entry.investigation_id not in known:
                    errors.append(f"varied.json: {entry.investigation_id} is not in investigations.json")
                elif entry.n_tests != per_inv.get(entry.investigation_id, 0):
                    errors.append(f"varied.json: {entry.investigation_id} counts {entry.n_tests} tests, "
                                  f"tests.json has {per_inv.get(entry.investigation_id, 0)}; rebuild it")
    gravity_path = folder / "gravity.json"
    if gravity_path.exists():
        gravity = _load(gravity_path, GravityFile, errors)
        if gravity is not None:
            named = {q.investigation_id for q in gravity.statements + gravity.stand_in.evidence}
            named |= {r.investigation_id for r in gravity.paired_results} | {gravity.stand_in.investigation_id}
            for psi_id in sorted(named - known):
                errors.append(f"gravity.json: {psi_id} is not in investigations.json")
            micro = next((level for level in gravity.levels if level.id == "microgravity"), None)
            if micro is not None and micro.n_tests != len(tests):
                errors.append(f"gravity.json counts {micro.n_tests} microgravity tests, tests.json has {len(tests)}; rebuild it")
    return errors, warnings


def _show(label: str, items: list[str]) -> None:
    for item in items[:MAX_SHOWN]:
        print(f"{label}: {item}")
    if len(items) > MAX_SHOWN:
        print(f"{label}: ... and {len(items) - MAX_SHOWN} more")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "folder", nargs="?", type=Path, default=None,
        help="folder holding investigations.json and tests.json (default: data/out)",
    )


def run(args: argparse.Namespace) -> int:
    folder = getattr(args, "folder", None)
    if folder is None:
        folder = config.OUT_DIR
        if not any((folder / n).exists() for n in ("investigations.json", "tests.json")):
            print("validate: no output files in data/out yet; nothing to check.")
            return 0
    errors, warnings = check(folder)
    _show("warning", warnings)
    _show("ERROR", errors)
    if errors:
        print(f"validate: FAILED with {len(errors)} error(s) in {folder}")
        return 1
    print(f"validate: OK ({folder})")
    return 0
