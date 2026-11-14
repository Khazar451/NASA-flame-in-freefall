# Pipeline implementation plan: data and AI (Khazar)

**Branch:** `pipeline` (mine only). Nobody else commits to it; I don't commit to theirs.
**Deadline:** Sun 15 Nov, 23:59 local. Target: submitted by **22:00**.
**Working rule:** each phase ends at a **gate**. Nothing in the next phase starts until Khazar has run the gate checks and said "verified".

## Decisions (Sat 14 Nov)

- **AI provider: Gemini, free tier (Google AI Studio), for everything.** No spend. All calls go through `llm.py`, so the provider can still be swapped with two lines in `.env`.
- **`/ask` never depends on the AI being up.** Fallback chain: Gemini → `demo_cache.json` → a retrieval-only answer (matching table rows plus the top cited passages, no model).
- **Data source: the PSI JSON API at `psi.nasa.gov`, no login.** The old `psi.ndc.nasa.gov` address is gone and the `s3://nasa-psi` bucket has no combustion data.
  - List: `GET /geode-py/ws/repo/search?term=&source=psi&type=investigation&size=500`, keep `Research Area == "Combustion Science"`
  - One record: `GET /geode-py/ws/repo/investigations/PSI-<n>`
  - One file: `GET /geode-py/ws/studies/PSI-<n>/download?file=<name>&version=<v>&redirect=false` returns a short-lived signed URL; GET that
- **Confirmed: 24 combustion investigations** (18 flight, 6 ground studies reusing flight data).

---

## Ground rules

### Git
- Work only on `pipeline`. Each verified phase becomes **one small PR into `main`**, reviewed by CompE #2.
- Rebase `pipeline` on `main` before opening each PR.
- Files this branch may touch: `pipeline/**`, `data/out/**`, `data/sample.json`, `data/schema.json`.
- `data/sample.json` and `data/schema.json` are the shared contract with the team. Changing either after Phase 2 needs a heads-up to both teammates first.
- Raw downloads never go into git: `data/raw/` and `data/interim/` each contain their own `.gitignore` (`*` and `!.gitignore`), so the shared root `.gitignore` stays untouched.

### Data
1. **No unchecked AI-typed numbers reach `data/out/`.**
   - Investigation-level values are checked by the ME teammate.
   - Test-level numbers come only from plain code applying a mapping file I reviewed.
2. **Every value records its source:** `source_id` plus page or row.
3. **Human corrections live in `data/overrides.json`**, applied last on every run. Re-running the pipeline never erases them.
4. **Every AI call is cached on disk**, keyed by a hash of the prompt. Re-runs are free and give the same results.
5. **One command rebuilds everything:** `python -m pipeline.run_all` turns `data/raw/` into `data/out/`.

### Layout
```
pipeline/
  implementation_plan.md   ← this file
  config.py                paths, env vars
  llm.py                   provider interface + disk cache + structured output
  models.py                Pydantic schema (the source of truth)
  validate.py              schema + sanity-range checks
  inventory.py             PSI API → inventory.csv
  fetch.py                 downloads + manifest
  extract_text.py          PDF → page-numbered text
  extract_investigations.py
  mappings/*.yaml          column mappings per table (AI proposes, I review)
  transform_tests.py
  score.py  coverage.py  varied.py
  build_index.py  api.py
  run_all.py
  tests/
data/
  schema.json  sample.json  overrides.json
  raw/  interim/           (gitignored)
  out/                     investigations.json, tests.json, rankings.json, coverage.json, varied.json
```

### What CompE #2 consumes
- **Static files:** everything in `data/out/*.json`.
- **One live endpoint:** `POST /ask {question}` → `{answer, citations: [{source_id, page|row, snippet}]}`.

---

## Phase 0: Branch and skeleton (Sat, done by 13:45)

**Build:**
- Branch `pipeline`.
- `requirements.txt`, `config.py`, `run_all.py` with subcommands as stubs.
- `llm.py`: one interface, `complete_json(prompt, schema) -> dict`, with a disk cache and a fake provider for tests.
- The `.gitignore` files inside `data/raw/` and `data/interim/`.
- A venv at `pipeline/.venv` (ignored by `pipeline/.gitignore`).

**Gate (Khazar checks):**
- [ ] `git branch --show-current` prints `pipeline`
- [ ] `python -m pipeline.run_all --help` lists the subcommands
- [ ] `pytest pipeline -q` passes: the cache returns a hit on the second identical call and the fake provider works
- [ ] `python -m pipeline.llm --check` prints `OK provider=gemini …`. The key is in `pipeline/.env` and not in git.

---

## Phase 1: Inventory from the PSI API (Sat 13:45–14:15)

**Build:**
- `inventory.py`: calls the search endpoint, keeps the combustion investigations, fetches each record into `data/raw/<psi_id>/record.json` and writes `pipeline/inventory.csv`.
- Columns: `psi_id, acronym, title, project_type, hardware, doi, related, n_tables, table_files, n_docs, docs_mb, n_publications, raw_gb, hardware_family, notes`.
- `hardware_family` and `notes` are filled by hand (ACME / MDCA droplets / glovebox flow duct / Saffire / smoke and aerosol / ground study); a re-run keeps them.
- Long timeout and retries: PSI-23 and PSI-39 are multi-megabyte records that take over a minute.

**Gate:**
- [ ] `inventory.csv` has 24 rows and every `psi_id` opens on the PSI site
- [ ] For each investigation we know its tables, documents and their sizes
- [ ] Investigations with no documents are listed, with where their O₂ / pressure / flow ranges will come from instead
- [ ] Test-level priority confirmed: flow-duct family (BASS, BASS-II, SPICE, SLICE) unless the inventory says otherwise
- [ ] PR #0 merged (skeleton + inventory)

---

## Phase 2: Schema contract (Sat 14:15–15:00, with the ME teammate)

**Build `models.py`:**
- `SourcedValue{value, unit, source_id, locator, snippet, status: source|extracted|verified|corrected}` (`source` = copied unchanged from the PSI record)
- `Range{min, max, unit}`
- `Investigation`:
  - id, name, psi_id, hardware_family
  - fuel_phase, fuel_material, sample_geometry
  - o2_range plus `o2_context: ambient | oxidizer_jet`
  - pressure_range, flow_range plus `flow_kind: forced | quiescent | stefan`
  - diluent, gravity
  - sensors, objectives
  - pillar: prevention | detection | suppression
  - links
- `Test{test_id, investigation_id, family, conditions…}`
- `Measurement{test_id, metric, value, unit, source_row}` (one row per measured result)
- `Habitat{name, o2_pct, pressure_psi, flow_regime, …}`

**Also build:**
- `data/schema.json`, exported from the models
- `data/sample.json`: about 10 hand-filled rows
- `validate.py`

**Gate:**
- [ ] The ME teammate signs off on fields and units
- [ ] CompE #2 signs off on the JSON shape
- [ ] `python -m pipeline.validate data/sample.json` passes
- [ ] **PR #1 merged to `main`** (schema + sample), so CompE #2 can build against it immediately

---

## Phase 3: Fetch and text extraction (Sat 15:00–17:00)

**Build:**
- `fetch.py`: reads each `record.json`, asks the download endpoint for a signed URL per file, downloads into `data/raw/<psi_id>/` and writes `data/raw/manifest.json` (file name, version, sha256, bytes, fetched_at). Safe to re-run.
- Downloads only these categories: experimental tables, science documents, engineering documents, reports. **Never `Raw Data`** (ACME Flame Design and CFI-G are about 2 TB each). Per-file size cap as a second guard.
- Signed URLs expire and are never stored; the manifest keeps the file name and version instead.
- `extract_text.py`: PyMuPDF turns each PDF into `data/interim/text/<source_id>.jsonl`, one line per page `{source_id, page, text}`. Also collects publication lists and abstracts.

**Gate:**
- [ ] Every inventory row that has files shows up in the manifest; nothing over the size limit was downloaded
- [ ] The text from 3 random pages, checked by eye, matches the PDF
- [ ] Re-running `fetch` downloads nothing new

---

## Phase 4: Investigation-level extraction (Sat 17:00–20:00)

**Build:**
- `extract_investigations.py` fills `Investigation` records in two passes:
  - **Copied from the PSI record, no AI:** name, acronym, objectives, approach, hypothesis, hardware, DOI, related investigations, Earth benefits, publications. Status `source`.
  - **AI extraction:** O₂, pressure and flow ranges, fuel, geometry, diluent, and the pillar / context tags, read from the record text, the documents and the experimental-table headers. Every field is a `SourcedValue` with a snippet quoted from the source.
- Rejection rules:
  - a snippet that doesn't appear in the source text means the field is cleared
  - a unit that doesn't match the schema means the field is cleared
- `review_export`: writes `data/interim/review.csv` for the ME teammate (one row per field, with columns for status and correction).
- `apply_overrides`: turns the reviewed CSV into `data/overrides.json`, merged last.
- Output: `data/out/investigations.json`.

**Gate:**
- [ ] All investigations pass validation; empty fields are reported, not invented
- [ ] Snippet check: 0 fields kept whose snippet isn't in the source
- [ ] I check 3 investigations end to end against their PDFs
- [ ] The ME teammate starts the full review (it can continue alongside Phase 5)
- [ ] PR #2 merged

---

## Phase 5: Test level for the flow-duct family (Sat 20:00–00:30)

**Build:**
- `propose_mapping`: the AI reads each table's header and first 5 rows and writes `mappings/<table>.yaml` (column → field, unit conversion, how to read the results). **I review and edit every YAML file.**
- `transform_tests.py`: plain code applies the mappings and writes `tests.json` (conditions + measurements, one measured result per row). Every row keeps `source_file` and `source_row`.
- Sanity ranges in `validate.py`:
  - O₂ between 0 and 100%
  - pressure between 0 and 20 psi
  - flow at least 0
  - every metric is in the allowed list for its family

**Gate:**
- [ ] Row count per table equals the source table's row count (minus documented skips)
- [ ] 5 random output rows traced back by hand to the raw file
- [ ] **End-to-end check with CompE #2:** 2 real investigations show on the atmosphere map at the deployed URL
- [ ] PR #3 merged

*Sleep 01:00–07:00.*

---

## Phase 6: Derived data (Sun 07:00–10:00)

**Build:**
- `score.py`: reads `docs/scoring.yaml` (written by the ME teammate) and writes `rankings.json` with each score broken into its parts: atmosphere overlap, fuel match, flow match, pillar.
- Each ranking's explanation is generated **ahead of time**, from the breakdown and the record only. Explanations are cached; nothing calls the AI live.
- `coverage.py` writes `coverage.json`: a grid of fuel phase × O₂ / pressure band × gravity, counting tests per cell. Empty cells are the gaps.
- `varied.py` writes `varied.json`: for each test-level investigation, which conditions changed and which stayed constant.

**Gate:**
- [ ] The ME teammate agrees with the top 5 for each habitat; any surprises are explained by the score breakdown
- [ ] Every explanation mentions only factors that appear in its breakdown
- [ ] The empty cells in `coverage.json` match the gaps the ME teammate expects
- [ ] PR #4 merged

---

## Phase 7: Q&A (Sun 10:00–14:00)

**Build:**
- `build_index.py`: splits page text and abstracts into chunks, each tagged with `source_id` and page, and builds a persistent ChromaDB index.
- `api.py`: FastAPI with `POST /ask`. The model can call three tools:
  - `query_investigations(filters)`
  - `query_tests(filters)`
  - `search_docs(q, k)`
- After answering, code drops any citation that doesn't appear in the tool results.
- Questions the data can't answer (for example partial gravity) get an explicit "no data" reply that names what bounds the answer.
- `demo_cache.json`: stored answers to the 10 demo questions, used if the AI provider fails.
- Retrieval-only fallback: for any other question when the provider fails or the free-tier rate limit is hit, return the matching rows and the top cited passages without a model-written answer.
- Rate limit; the API key comes from an environment variable.

**Gate:**
- [ ] All 10 demo questions answered; every citation opens to the right page or row
- [ ] Every number in an answer matches `tests.json` or `investigations.json`
- [ ] Partial-gravity question → honest "no data" reply plus what bounds it
- [ ] Provider disabled → cached answers are served for demo questions, retrieval-only answers for the rest
- [ ] PR #5 merged

---

## Phase 8: Extras if time allows (Sun 14:00–18:00), then freeze

**Extras, in priority order:**
1. Data for the partial-gravity panel
2. Test level for droplets (FLEX, CFI), only if 1 is done
3. Data for the hardware graph

**Build:** a clean rebuild check, with versions pinned in `requirements.txt`.

**Gate (18:00 data freeze):**
- [ ] Fresh clone → `pip install -r pipeline/requirements.txt` → `python -m pipeline.run_all` → the same file checksums in `data/out/`
- [ ] Final PR merged. After this, bug fixes only.

**After the freeze:** help with deployment, record the demo at 20:00, **submit by 22:00**.

---

## Where I depend on teammates

| When | From | What |
|---|---|---|
| Sat 14:15 | ME, CompE #2 | Schema and JSON shape sign-off |
| Sat ~20:00 onward | ME | Review of the investigation-level CSV |
| Sat 20:00 | ME | Which metrics each family records (flow-duct first) |
| Sun 07:00 | ME | `docs/scoring.yaml` |
| Sat ~00:30 | CompE #2 | End-to-end check on the deployed URL |
