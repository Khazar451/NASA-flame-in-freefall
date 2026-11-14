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

    # Pipeline extras: values only some experiment families report, e.g.
    # {"burning_rate_mm2_s": 0.52, "oxidizer_o2_pct": 40}
    extra: dict[str, ExtraValue] | None = None


class InvestigationsFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    investigations: list[Investigation]


class TestsFile(_Strict):
    meta: dict[str, Any] = Field(default_factory=dict)
    tests: list[TestRow]


def json_schema() -> dict[str, Any]:
    return {
        "investigations.json": InvestigationsFile.model_json_schema(),
        "tests.json": TestsFile.model_json_schema(),
    }


def schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    SCHEMA_JSON.write_text(schema_text(), encoding="utf-8")
    print(f"Wrote {SCHEMA_JSON.relative_to(config.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
