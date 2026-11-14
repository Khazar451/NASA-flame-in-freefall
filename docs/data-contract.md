# Data contract

The pipeline writes two JSON files to `data/out/`. The dashboard copies them into `app/public/data/` when it starts or builds (`npm run sync-data`) and reads them from there. The TypeScript version of this contract is `app/src/types.ts`; change both together.

The pipeline also adds optional fields the dashboard does not depend on: `hardware_family`, `sub_area`, `objectives`, `earth_benefits` and `n_publications` on an investigation, and `extra` (family-specific values such as `burn_time_s`) on a test. They are listed in `app/src/types.ts` as optional.

## investigations.json

```
{
  "meta": { "roles_reviewed": false },
  "investigations": [ Investigation, ... ]
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | PSI accession, e.g. `"PSI-25"` |
| `acronym` | string or null | e.g. `"BASS-II"`. Null for ground studies without one |
| `title` | string | |
| `project_type` | `"flight"` or `"ground"` | |
| `role` | `"prevention"`, `"detection"`, `"suppression"` or `"fundamentals"` | Set by the domain lead |
| `fuel_phase` | `"solid"`, `"liquid"`, `"gas"` or `"other"` | |
| `hardware` | string or null | |
| `related` | string[] | PSI ids |
| `doi` | string or null | Without the `https://doi.org/` prefix |
| `url` | string | PSI landing page |

Set `meta.roles_reviewed` to `true` once the role tags have been checked. That removes the "Draft settings" note on the ranking page.

## tests.json

```
{ "meta": { ... }, "tests": [ TestRow, ... ] }
```

One row per test run.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Unique. Suggested: `"<investigation id>:<test label>"` |
| `investigation_id` | string | Must match an `Investigation.id` |
| `test_label` | string | As named in the source table |
| `fuel_material` | string or null | Free text from the source |
| `fuel_phase` | same values as above | |
| `o2_pct` | number or null | Ambient oxygen, % by volume |
| `pressure_kpa` | number or null | Total pressure in kPa (1 atm = 101.325, 1 psi = 6.895) |
| `flow_cm_s` | number or null | 0 for a still atmosphere |
| `diluent` | string or null | e.g. `"N2"`, `"CO2"`, `"He"` |
| `outcome` | `"sustained"`, `"extinguished"`, `"no_ignition"` or `"unknown"` | |
| `note` | string or null | Shown in the hover box. Use it for assumptions, e.g. an assumed pressure |
| `source` | `{ "file": string, "row": number }` or null | Where the values came from |
| `sample` | boolean | `true` only for placeholder rows. Real rows must be `false` |
| `assumed` | list of `"o2_pct"`, `"pressure_kpa"`, `"flow_cm_s"` | The fields whose value is assumed rather than taken from the source table, e.g. `["o2_pct", "pressure_kpa"]` for ISS cabin air. Empty when nothing is assumed. Every listed field has a value |

Rows need both `o2_pct` and `pressure_kpa` to appear on the atmosphere map and to count toward the atmosphere part of the ranking score. Leave a value `null` when the source does not give it; do not guess. If a value is assumed, list it in `assumed` and say why in `note`.
