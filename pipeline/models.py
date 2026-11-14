"""The data contract, as Pydantic models.

These mirror docs/data-contract.md and app/src/types.ts (owned by the dashboard) field
for field. Fields listed under "Pipeline extras" are optional additions the dashboard is
free to ignore. Change the contract documents first, then this file.

    python -m pipeline.models      rewrite data/schema.json from these models
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline import config

SCHEMA_JSON = config.DATA_DIR / "schema.json"

Role = Literal["prevention", "detection", "suppression", "fundamentals"]
FuelPhase = Literal["solid", "liquid", "gas", "other"]
Outcome = Literal["sustained", "extinguished", "no_ignition", "unknown"]
ProjectType = Literal["flight", "ground"]

PSI_ID = r"^PSI-\d+$"
ExtraValue = float | int | str | bool | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Investigation(_Strict):
    id: str = Field(pattern=PSI_ID, description='PSI accession, e.g. "PSI-25"')
    acronym: str | None
    title: str = Field(min_length=1)
    project_type: ProjectType
    role: Role
    fuel_phase: FuelPhase
    hardware: str | None
    related: list[str]
    doi: str | None = Field(description="Without the https://doi.org/ prefix")
    url: str

    # Pipeline extras
    hardware_family: str | None = None
    sub_area: str | None = None
    objectives: str | None = None
    earth_benefits: str | None = None
    n_publications: int | None = Field(default=None, ge=0)


class Source(_Strict):
    file: str = Field(min_length=1)
    row: int = Field(ge=1, description="1 = first data row under the header")


class TestRow(_Strict):
    id: str = Field(min_length=1)
    investigation_id: str = Field(pattern=PSI_ID)
    test_label: str
    fuel_material: str | None
    fuel_phase: FuelPhase
    o2_pct: float | None = Field(description="Ambient oxygen, % by volume")
    pressure_kpa: float | None = Field(description="Total pressure")
    flow_cm_s: float | None = Field(description="0 for a still atmosphere")
    diluent: str | None
    outcome: Outcome
    note: str | None
    source: Source | None
    sample: bool = Field(description="True only for placeholder rows")
    assumed: list[Literal["o2_pct", "pressure_kpa", "flow_cm_s"]] = Field(
        default_factory=list,
        description="Fields whose value is not in the source table but assumed (the note says why); "
                    "empty when nothing is assumed",
    )

    # Pipeline extras: values only some experiment families report, e.g.
    # {"burning_rate_mm2_s": 0.52, "oxidizer_o2_pct": 40}
    extra: dict[str, ExtraValue] | None = None


class InvestigationsFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    investigations: list[Investigation]


class TestsFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    tests: list[TestRow]


# ---------------------------------------------------------------------------------
# Pipeline-only files (Phase 6). These are not part of docs/data-contract.md yet; the
# dashboard can use them for the gap map and the "what varied" view.

Habitat = Literal["cabin", "lunar", "suit"]  # the ids in app/src/habitats.ts


class Band(_Strict):
    id: str
    label: str
    min: float = Field(description="Inclusive lower edge")
    max: float | None = Field(description="Exclusive upper edge; null = no upper edge")
    habitats: list[Habitat] = Field(description="Habitats whose range overlaps this band")


class CoverageCell(_Strict):
    fuel_phase: FuelPhase
    o2_band: str
    pressure_band: str
    n_tests: int = Field(ge=0, description="0 = a gap: nothing tested here")
    n_pressure_assumed: int = Field(ge=0, description="Tests whose pressure is assumed, not measured")
    investigations: list[str]
    outcomes: dict[str, int]
    habitats: list[Habitat] = Field(description="Habitats inside both bands of this cell")


class OffGrid(_Strict):
    fuel_phase: FuelPhase
    reason: str
    n_tests: int = Field(ge=0)
    investigations: list[str]


class CoverageFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    o2_bands: list[Band]
    pressure_bands: list[Band]
    cells: list[CoverageCell]
    not_on_grid: list[OffGrid]


class ConditionSummary(_Strict):
    field: str = Field(description='A TestRow field, or "extra.<key>"')
    label: str
    unit: str | None
    status: Literal["varied", "constant", "not_recorded"]
    assumed: bool = Field(description="The value is an assumption, not from the source table")
    n_with_value: int = Field(ge=0)
    min: float | None
    max: float | None
    levels: list[float | str] = Field(description="Distinct values (numbers rounded to `precision`), at most 12")
    n_levels: int = Field(ge=0)
    precision: float | None


class InvestigationVariation(_Strict):
    investigation_id: str = Field(pattern=PSI_ID)
    n_tests: int = Field(ge=0)
    summary: str
    conditions: list[ConditionSummary]
    outcomes: dict[str, int]


class VariedFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    investigations: list[InvestigationVariation]


class Quote(_Strict):
    investigation_id: str = Field(pattern=PSI_ID)
    text: str = Field(min_length=1, description="Copied word for word from the source")
    source_label: str
    url: str


class GravityLevel(_Strict):
    id: Literal["microgravity", "moon", "mars", "earth"]
    label: str
    g: float = Field(ge=0, description="Approximate gravity as a fraction of Earth's")
    status: Literal["tested", "no_data", "comparison_only"]
    n_tests: int = Field(ge=0, description="Test rows in tests.json at this gravity level")
    investigations: list[str]
    note: str | None


class PairedResult(_Strict):
    investigation_id: str = Field(pattern=PSI_ID)
    sample: str
    material: str | None
    flow_direction: str | None
    microgravity_burn_length: str | None
    microgravity_spread_rate: str | None
    normal_gravity_burn_length: str | None
    normal_gravity_spread_rate: str | None
    source: Source


class StandIn(_Strict):
    investigation_id: str = Field(pattern=PSI_ID)
    summary: str
    evidence: list[Quote]


class Upcoming(_Strict):
    name: str
    note: str
    source_label: str
    url: str


class GravityFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    summary: str
    levels: list[GravityLevel]
    stand_in: StandIn
    paired_results: list[PairedResult]
    paired_summary: str = Field(description="Counted from paired_results by plain code")
    paired_notes: list[str] = Field(description="Footnotes of the source table, word for word")
    statements: list[Quote] = Field(description="What each PSI record says about gravity, word for word")
    upcoming: list[Upcoming]


class Finding(_Strict):
    investigation_id: str = Field(pattern=PSI_ID)
    finding: str = Field(min_length=1, description="One plain sentence; every number in it is in the quote")
    quote: str = Field(min_length=1, description="Copied word for word from the page")
    source_id: str
    page: int | None = Field(description="None for a PSI record section or a Word file part")
    section: str | None = None
    url: str


class FindingsFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding]


def json_schema() -> dict[str, Any]:
    return {
        "investigations.json": InvestigationsFile.model_json_schema(),
        "tests.json": TestsFile.model_json_schema(),
        "coverage.json": CoverageFile.model_json_schema(),
        "varied.json": VariedFile.model_json_schema(),
        "gravity.json": GravityFile.model_json_schema(),
        "findings.json": FindingsFile.model_json_schema(),
    }


def schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    SCHEMA_JSON.write_text(schema_text(), encoding="utf-8")
    print(f"Wrote {SCHEMA_JSON.relative_to(config.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
