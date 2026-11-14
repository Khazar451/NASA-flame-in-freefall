"""Phase 6: build data/out/varied.json, what varied and what stayed the same.

For every investigation this lists its test conditions and, for each one, whether it
was varied across the tests, held constant, or not recorded. Numbers are compared
after rounding to a stated precision, so measurement noise (101.3 vs 101.4 kPa) does
not count as variation. A value the pipeline assumed (the cabin pressure of BASS-II)
is reported as assumed, never as a measured constant.

Results such as burn time are not conditions and are left out; outcome counts are
listed separately. Investigations with no test rows are listed with the reason.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

import yaml

from pipeline import config, transform_tests
from pipeline.models import VariedFile

OUT = config.OUT_DIR / "varied.json"
MAX_LEVELS = 12

# (field, label, unit, precision). precision None = text, compared after normalising case
# and spacing. Fields starting with "extra." are read from the row's extra values.
CONDITIONS: list[tuple[str, str, str | None, float | None]] = [
    ("fuel_material", "Fuel", None, None),
    ("o2_pct", "Oxygen", "%", 1.0),
    ("pressure_kpa", "Pressure", "kPa", 5.0),
    ("flow_cm_s", "Flow speed", "cm/s", 1.0),
    ("diluent", "Diluent gas", None, None),
    ("extra.flame_type", "Flame type", None, None),
    ("extra.oxidizer_o2_pct", "Oxygen fed through the burner", "%", 1.0),
    ("extra.fuel_mole_fraction", "Fuel fraction in the burner gas", None, 0.01),
    ("extra.ambient_fuel_mole_fraction", "Fuel fraction in the atmosphere", None, 0.01),
    ("extra.flow_direction", "Flow direction", None, None),
    ("extra.nozzle_mm", "Nozzle diameter", "mm", 0.01),
    ("extra.droplet_diameter_mm", "Initial droplet diameter", "mm", 0.5),
    ("extra.fiber_supported", "Droplet on a support fibre", None, None),
    ("extra.sample_thickness", "Sample thickness", None, None),
    ("extra.sample_thickness_cm", "Sample thickness", "cm", 0.01),
    ("extra.heating_temperature_ratio", "Heater setting (Rt/R0)", None, 0.05),
]
NAMES = {"o2_pct": "oxygen", "pressure_kpa": "pressure", "flow_cm_s": "flow speed"}


def value_of(row: dict[str, Any], field: str) -> Any:
    if field.startswith("extra."):
        return (row.get("extra") or {}).get(field[len("extra."):])
    return row.get(field)


def _level(value: Any, precision: float | None) -> float | str:
    if precision is None:
        return " ".join(str(value).split()).lower()
    rounded = round(round(float(value) / precision) * precision, 6)
    return int(rounded) if rounded == int(rounded) else rounded


def summarise(rows: list[dict[str, Any]], assumed: set[str]) -> list[dict[str, Any]]:
    """One ConditionSummary per condition that applies to these rows."""
    out = []
    for field, label, unit, precision in CONDITIONS:
        values = [v for v in (value_of(r, field) for r in rows) if v is not None]
        if not values and field.startswith("extra."):
            continue  # extras only appear for the families that report them
        if precision is not None:
            values = [float(v) for v in values]
        # Text is compared case- and space-insensitively but shown as first written.
        shown: dict[float | str, float | str] = {}
        for v in values:
            shown.setdefault(_level(v, precision), v if precision is None else _level(v, precision))
        levels = [shown[k] for k in sorted(shown, key=lambda x: (isinstance(x, str), x))]
        status = "not_recorded" if not values else ("varied" if len(levels) > 1 else "constant")
        out.append({
            "field": field, "label": label, "unit": unit, "status": status,
            "assumed": field in assumed and bool(values),
            "n_with_value": len(values),
            "min": min(values) if values and precision is not None else None,
            "max": max(values) if values and precision is not None else None,
            "levels": levels[:MAX_LEVELS], "n_levels": len(levels), "precision": precision,
        })
    return out


def _fmt(x: float) -> str:
    return f"{x:.3g}" if abs(x) < 100 else f"{x:.0f}"


def describe(c: dict[str, Any]) -> str:
    name = NAMES.get(c["field"], c["label"].lower())
    unit = f" {c['unit']}" if c["unit"] else ""
    if c["min"] is not None:
        if c["status"] == "varied":
            return f"{name} {_fmt(c['min'])}–{_fmt(c['max'])}{unit} ({c['n_levels']} levels)"
        return f"{name} {_fmt(c['min'])}{unit}"
    if c["status"] == "varied":
        return f"{name} ({c['n_levels']} different entries)"
    return f"{name} {c['levels'][0]}"


def summary_text(conditions: list[dict[str, Any]], n_tests: int) -> str:
    parts = []
    groups = (
        ("Varied", [c for c in conditions if c["status"] == "varied"]),
        ("Constant", [c for c in conditions if c["status"] == "constant" and not c["assumed"]]),
        ("Assumed, not in the table", [c for c in conditions if c["assumed"]]),
    )
    for title, items in groups:
        if items:
            parts.append(f"{title}: " + "; ".join(describe(c) for c in items) + ".")
    missing = [NAMES.get(c["field"], c["label"].lower()) for c in conditions if c["status"] == "not_recorded"]
    if missing:
        parts.append("Not recorded: " + ", ".join(missing) + ".")
    partial = [
        f"{NAMES.get(c['field'], c['label'].lower())} ({c['n_with_value']} of {n_tests})"
        for c in conditions if 0 < c["n_with_value"] < n_tests and not c["field"].startswith("extra.")
    ]
    if partial:
        parts.append("Recorded for some tests only: " + ", ".join(partial) + ".")
    return " ".join(parts)


def skipped_reasons() -> dict[str, str]:
    path = transform_tests.MAPPINGS_DIR / transform_tests.SKIPPED_FILE
    if not path.exists():
        return {}
    return {e["investigation"]: e["reason"] for e in yaml.safe_load(path.read_text(encoding="utf-8")) or []}


def build(tests: list[dict[str, Any]], investigation_ids: list[str],
          assumed: list[tuple[str, str, set[str]]], skipped: dict[str, str]) -> dict[str, Any]:
    entries = []
    for psi_id in investigation_ids:
        rows = [t for t in tests if t["investigation_id"] == psi_id]
        if not rows:
            reason = skipped.get(psi_id, "PSI has no experimental table for this investigation.")
            entries.append({"investigation_id": psi_id, "n_tests": 0, "conditions": [], "outcomes": {},
                            "summary": f"No test-level data. {reason}"})
            continue
        # A field counts as assumed only if every row that has it got it from an assumption.
        per_row = [transform_tests.assumed_in(r, assumed) for r in rows]
        assumed_here = {
            f for f in transform_tests.NUMERIC_FIELDS
            if any(r[f] is not None for r in rows)
            and all(f in a for r, a in zip(rows, per_row) if r[f] is not None)
        }
        conditions = summarise(rows, assumed_here)
        entries.append({
            "investigation_id": psi_id, "n_tests": len(rows),
            "summary": summary_text(conditions, len(rows)),
            "conditions": conditions, "outcomes": dict(Counter(r["outcome"] for r in rows)),
        })
    meta = {
        "source": "data/out/tests.json",
        "rounding": "Numbers are compared after rounding to each condition's precision, so "
                    "measurement noise does not count as variation.",
    }
    return VariedFile.model_validate({"meta": meta, "investigations": entries}).model_dump(mode="json")


def run(args: argparse.Namespace) -> int:
    paths = [config.OUT_DIR / n for n in ("investigations.json", "tests.json")]
    if not all(p.exists() for p in paths):
        print("varied: investigations.json and tests.json are needed; run the earlier steps first.")
        return 1
    investigations = json.loads(paths[0].read_text(encoding="utf-8"))["investigations"]
    tests = json.loads(paths[1].read_text(encoding="utf-8"))["tests"]
    data = build(tests, [i["id"] for i in investigations], transform_tests.assumed_fields(), skipped_reasons())
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    with_tests = sum(e["n_tests"] > 0 for e in data["investigations"])
    print(f"varied: {with_tests} investigations with test rows, {len(data['investigations']) - with_tests} without")
    print(f"Wrote {OUT.relative_to(config.REPO_ROOT)}")
    return 0
