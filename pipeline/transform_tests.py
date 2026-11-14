"""Phase 5: build data/out/tests.json from the experimental tables.

Every table has a mapping file in pipeline/mappings/ that says which column feeds which
field and how to convert it. This module only applies those mappings: no AI is involved
and no number is typed by hand, so every value can be traced to a source file and row.

A mapping file (YAML):

  investigation: PSI-69
  table: FLEX.csv                # the end of the file name in data/raw/<id>/tables/
  fuel_phase: liquid
  null_values: ["–"]             # cell texts that mean "no value"
  keep_if_not_blank: [Test]      # optional: drop rows where any of these is blank
  skip_if_startswith: {Material: ["Pre-test"]}   # optional
  label: {column: "FLEX Test #"} # or {template: "run {row}"}
  fuel_material: {column: Fuel}
  o2_pct: {column: "O ...; mole fraction", scale: 100}
  pressure_kpa: {column: "Ambient pressure; mmHg", scale: 0.1333224}
  flow_cm_s: null                # the table does not give it
  diluent: {components: {N2: "N ...", CO2: "CO ..."}}   # or {value: N2}
  outcome: {column: "Test end", map: {Extinction: extinguished}}
  note: "Shown on hover."
  extra: {burn_time_s: {column: "Burn time; s"}, test_end: {column: "Test end", type: text}}

A field is given as {column: ..., scale: ..., parse: number | first_number | range_mean}
or as a constant {value: ..., assumed: "why"}; an assumed value adds its reason to the
row's note. A cell that is not blank, not a listed null value and still cannot be read is
counted and reported, so a wrong mapping shows up instead of silently producing nulls.

The source tables contain a few values that cannot be real (a pressure of 0, a negative
flow speed). Those are left empty, the row's note quotes the source value, and the
build summary counts them.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from pipeline import config, inventory, validate
from pipeline.models import TestRow, TestsFile

MAPPINGS_DIR = config.PIPELINE_DIR / "mappings"
SKIPPED_FILE = "_skipped.yaml"  # tables deliberately left out, with the reason
OUT = config.OUT_DIR / "tests.json"

NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
NUMERIC_FIELDS = ("o2_pct", "pressure_kpa", "flow_cm_s")
# field -> (limits, is the lower limit itself invalid?, what to call it in a note)
LIMITS = {
    "o2_pct": (validate.O2_PCT, False, "oxygen"),
    "pressure_kpa": (validate.PRESSURE_KPA, True, "pressure"),
    "flow_cm_s": (validate.FLOW_CM_S, False, "flow speed"),
}
OUTCOMES = {"sustained", "extinguished", "no_ignition", "unknown"}


class MappingError(ValueError):
    """A mapping file does not fit its table."""


def read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    """Header and rows of a PSI table. Spacing is normalised; some files are not UTF-8."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    rows = [[" ".join(cell.split()) for cell in row] for row in csv.reader(io.StringIO(text))]
    return rows[0], rows[1:]


def find_table(psi_id: str, ending: str) -> Path:
    folder = config.RAW_DIR / psi_id / "tables"
    matches = sorted(p for p in folder.glob("*.csv") if p.name.endswith(ending))
    if len(matches) != 1:
        raise MappingError(f"{psi_id}: expected one table ending in {ending!r} in {folder}, found {len(matches)}")
    return matches[0]


class Table:
    """One source table plus the bookkeeping for cells that could not be read."""

    def __init__(self, mapping: dict[str, Any], path: Path) -> None:
        self.mapping = mapping
        self.path = path
        self.header, self.rows = read_table(path)
        self.columns = {name: index for index, name in enumerate(self.header)}
        self.nulls = {str(v).lower() for v in mapping.get("null_values", [])} | {""}
        self.unreadable: dict[str, Counter[str]] = defaultdict(Counter)
        self.out_of_range: Counter[str] = Counter()

    def cell(self, row: list[str], column: str) -> str:
        if column not in self.columns:
            raise MappingError(f"{self.path.name}: no column named {column!r}; it has {self.header}")
        index = self.columns[column]
        return row[index] if index < len(row) else ""

    def is_null(self, text: str) -> bool:
        return text.lower() in self.nulls

    def number(self, row: list[str], spec: dict[str, Any], field: str) -> float | None:
        if "value" in spec:
            return None if spec["value"] is None else float(spec["value"])
        text = self.cell(row, spec["column"])
        if self.is_null(text):
            return None
        parse = spec.get("parse", "number")
        found = [float(n) for n in NUMBER.findall(text)]
        if parse == "number":
            try:
                value = float(text)
            except ValueError:
                value = None
        elif parse == "first_number":
            value = found[0] if found else None
        elif parse == "range_mean":
            value = sum(found) / len(found) if found else None
        else:
            raise MappingError(f"{self.path.name}: {field}: unknown parse {parse!r}")
        if value is None:
            self.unreadable[field][text] += 1
            return None
        return round(value * float(spec.get("scale", 1)) + float(spec.get("offset", 0)), 6)

    def text(self, row: list[str], spec: dict[str, Any] | None) -> str | None:
        if spec is None:
            return None
        if "value" in spec:
            return spec["value"]
        text = self.cell(row, spec["column"])
        return None if self.is_null(text) else text


def _keep(table: Table, row: list[str]) -> bool:
    mapping = table.mapping
    if not any(cell for cell in row):
        return False
    if any(not table.cell(row, column) for column in mapping.get("keep_if_not_blank", [])):
        return False
    for column, prefixes in mapping.get("skip_if_startswith", {}).items():
        if table.cell(row, column).lower().startswith(tuple(p.lower() for p in prefixes)):
            return False
    return True


def _label(table: Table, row: list[str], number: int) -> str:
    spec = table.mapping["label"]
    if "template" in spec:
        values = {name: table.cell(row, name) for name in re.findall(r"\{([^{}]+)\}", spec["template"]) if name != "row"}
        label = spec["template"].replace("{row}", str(number))
        for name, value in values.items():
            label = label.replace("{" + name + "}", value)
    else:
        label = table.cell(row, spec["column"])
    return label or f"row {number}"


def _diluent(table: Table, row: list[str], spec: dict[str, Any] | None) -> str | None:
    if spec is None:
        return None
    if "value" in spec:
        return spec["value"]
    parts = []
    for name, column in spec["components"].items():
        value = table.number(row, {"column": column}, f"diluent.{name}")
        if value:
            parts.append((value, name))
    # largest share first, e.g. "N2+CO2"
    return "+".join(name for _, name in sorted(parts, key=lambda p: -p[0])) or None


def _outcome(table: Table, row: list[str], spec: dict[str, Any] | None) -> str:
    if spec is None:
        return "unknown"
    if "value" in spec:
        return spec["value"]
    text = table.cell(row, spec["column"])
    known = {str(k).lower(): v for k, v in spec["map"].items()}
    if text.lower() in known:
        return known[text.lower()]
    if not table.is_null(text):
        table.unreadable["outcome"][text] += 1
    return "unknown"


def apply_mapping(mapping: dict[str, Any], path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn one table into contract rows. Return (rows, a report for the build summary)."""
    table = Table(mapping, path)
    psi_id = mapping["investigation"]
    rows: list[dict[str, Any]] = []
    for number, row in enumerate(table.rows, start=1):  # 1 = first row under the header
        if not _keep(table, row):
            continue
        notes = [mapping["note"]] if mapping.get("note") else []
        values: dict[str, float | None] = {}
        for field in NUMERIC_FIELDS:
            spec = mapping.get(field)
            values[field] = table.number(row, spec, field) if spec else None
            if spec and spec.get("assumed") and values[field] is not None:
                notes.append(spec["assumed"])
            limits, open_low, name = LIMITS[field]
            value = values[field]
            if value is not None and (value < limits[0] or value > limits[1] or (open_low and value == limits[0])):
                source = table.cell(row, spec["column"]) if "column" in spec else spec["value"]
                notes.append(f"The source table gives {name} as {source}, which is not possible; left empty.")
                table.out_of_range[field] += 1
                values[field] = None
        extra: dict[str, Any] = {}
        for key, spec in (mapping.get("extra") or {}).items():
            value = table.text(row, spec) if spec.get("type") == "text" else table.number(row, spec, f"extra.{key}")
            if value is not None:
                extra[key] = value
        label = _label(table, row, number)
        rows.append({
            "id": f"{psi_id}:{label}",
            "investigation_id": psi_id,
            "test_label": label,
            "fuel_material": table.text(row, mapping.get("fuel_material")),
            "fuel_phase": mapping["fuel_phase"],
            **values,
            "diluent": _diluent(table, row, mapping.get("diluent")),
            "outcome": _outcome(table, row, mapping.get("outcome")),
            "note": " ".join(notes) or None,
            "source": {"file": path.name, "row": number},
            "sample": False,
            "extra": extra or None,
        })
    report = {
        "investigation": psi_id,
        "file": path.name,
        "source_rows": len(table.rows),
        "rows": len(rows),
        "skipped_rows": len(table.rows) - len(rows),
        "unreadable": {field: dict(cells) for field, cells in sorted(table.unreadable.items())},
        "impossible_values_left_empty": dict(sorted(table.out_of_range.items())),
    }
    return rows, report


def assumed_fields() -> list[tuple[str, str, set[str]]]:
    """(investigation, table ending, fields) for every field a mapping fills with an assumption."""
    mappings, _ = load_mappings()
    found = []
    for mapping in mappings:
        fields = {
            field for field in NUMERIC_FIELDS
            if isinstance(mapping.get(field), dict) and mapping[field].get("assumed")
        }
        if fields:
            found.append((mapping["investigation"], mapping["table"], fields))
    return found


def assumed_in(row: dict[str, Any], table: list[tuple[str, str, set[str]]]) -> set[str]:
    """Which of a test row's values were assumed, given the result of assumed_fields()."""
    source = (row.get("source") or {}).get("file", "")
    return set().union(*(
        fields for psi_id, ending, fields in table
        if psi_id == row["investigation_id"] and source.endswith(ending)
    ))


def make_ids_unique(rows: list[dict[str, Any]]) -> int:
    """Some tables repeat a test label. Later repeats get ~2, ~3, ... Return how many changed."""
    seen: Counter[str] = Counter()
    changed = 0
    for row in rows:
        seen[row["id"]] += 1
        if seen[row["id"]] > 1:
            suffix = f"~{seen[row['id']]}"
            row["id"] += suffix
            row["test_label"] += suffix
            changed += 1
    return changed


def load_mappings() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mappings, skipped = [], []
    for path in sorted(MAPPINGS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if path.name == SKIPPED_FILE:
            skipped = data or []
        else:
            mappings.append({**data, "_file": path.name})
    mappings.sort(key=lambda m: (inventory.psi_number(m["investigation"]), m["table"]))
    return mappings, skipped


def build() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    mappings, skipped = load_mappings()
    rows: list[dict[str, Any]] = []
    reports = []
    for mapping in mappings:
        path = find_table(mapping["investigation"], mapping["table"])
        table_rows, report = apply_mapping(mapping, path)
        report["mapping"] = mapping["_file"]
        rows += table_rows
        reports.append(report)
    renamed = make_ids_unique(rows)
    return rows, reports, skipped, renamed


def run(args: argparse.Namespace) -> int:
    if not any(MAPPINGS_DIR.glob("*.yaml")):
        print("transform-tests: no mapping files in pipeline/mappings.")
        return 1
    try:
        rows, reports, skipped, renamed = build()
    except MappingError as exc:
        print(f"transform-tests: {exc}")
        return 1
    rows = [TestRow.model_validate(row).model_dump(mode="json") for row in rows]
    meta = {
        "source": "NASA Physical Sciences Informatics (psi.nasa.gov) experimental tables",
        "total_rows": len(rows),
        "sample_rows": 0,
        "rows_with_o2_and_pressure": sum(r["o2_pct"] is not None and r["pressure_kpa"] is not None for r in rows),
        "tables": reports,
        "tables_not_used": skipped,
    }
    payload = TestsFile(meta=meta, tests=rows).model_dump(mode="json")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    problems = 0
    for report in reports:
        print(
            f"  {report['investigation']:8} {report['rows']:>5} rows from {report['source_rows']:>5} "
            f"({report['skipped_rows']} skipped)  {report['file']}"
        )
        for field, cells in report["unreadable"].items():
            problems += sum(cells.values())
            examples = ", ".join(f"{text!r}x{n}" for text, n in list(cells.items())[:5])
            print(f"      could not read {field}: {examples}")
        for field, count in report["impossible_values_left_empty"].items():
            print(f"      {count} impossible {field} value(s) in the source left empty")
    print(
        f"transform-tests: {len(rows)} rows from {len(reports)} tables, {len(skipped)} tables not used, "
        f"{renamed} repeated labels renamed, {problems} unreadable cells"
    )
    print(f"Wrote {OUT.relative_to(config.REPO_ROOT)}")
    return 1 if problems else 0
