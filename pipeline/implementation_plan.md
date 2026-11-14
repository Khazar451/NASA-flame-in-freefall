# Pipeline implementation plan: data and AI (Khazar)

**Branch:** `pipeline` (mine only). Nobody else commits to it; I don't commit to theirs.
**Deadline:** Sun 15 Nov, 23:59 local. Target: submitted by **22:00**.
**Working rule:** each phase ends at a **gate**. Nothing in the next phase starts until Khazar has run the gate checks and said "verified".

## Decisions (Sat 14 Nov)

- **AI provider: Gemini, free tier (Google AI Studio), for everything.** No spend. All calls go through `llm.py`, so the provider can still be swapped with two lines in `.env`.
- **The free tier allows about 20 requests per model per day** (found in Phase 4). `LLM_MODEL` is therefore a list of models and `llm.py` moves to the next one when a quota runs out. With four models that is about 80 requests a day, so every AI result the build needs is saved to a committed file, and `/ask` must work well without the model.
- **`/ask` never depends on the AI being up.** Fallback chain: Gemini → `demo_cache.json` → a retrieval-only answer (matching table rows plus the top cited passages, no model).
- **Data source: the PSI JSON API at `psi.nasa.gov`, no login.** The old `psi.ndc.nasa.gov` address is gone and the `s3://nasa-psi` bucket has no combustion data.
  - List: `GET /geode-py/ws/repo/search?term=&source=psi&type=investigation&size=500`, keep `Research Area == "Combustion Science"`
  - One record: `GET /geode-py/ws/repo/investigations/PSI-<n>`
  - Full file list: `POST /geode-py/ws/files/v2` with `{folder: "", fileType: "study", studyId, version}` (the record's own `added` / `modified` lists only cover the latest version)
  - One file: `GET /geode-py/ws/studies/PSI-<n>/download?file=<name>&version=<v>&redirect=false` returns a short-lived signed URL; GET that
- **Confirmed: 24 combustion investigations** (19 flight, 5 ground studies reusing flight data).

---

## Ground rules

### Git
- Work only on `pipeline`. Each verified phase becomes **one small PR into `main`**, reviewed by CompE #2.
- Rebase `pipeline` on `main` before opening each PR.
- Files this branch may touch: `pipeline/**`, `data/out/**`, `data/schema.json`.
- The shared contract is `docs/data-contract.md` plus `app/src/types.ts`, owned by CompE #2. `pipeline/models.py` follows it; a contract change is agreed with both teammates first.
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
  models.py                Pydantic mirror of docs/data-contract.md
  validate.py              schema + sanity-range checks
  psi.py                   PSI API client
  inventory.py             PSI API → inventory.csv
  fetch.py                 downloads + manifest
  extract_text.py          PDF → page-numbered text
  extract_investigations.py
  mappings/*.yaml          column mappings per table (AI proposes, I review)
  transform_tests.py
  coverage.py  varied.py
  build_index.py  api.py
  run_all.py
  tests/
data/
  schema.json  overrides.json
  raw/  interim/           (gitignored)
  out/                     investigations.json, tests.json, coverage.json, varied.json
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
- `psi.py`: the API client (search, record, file tree, signed download URL), reused by `fetch.py`.
- `inventory.py`: calls the search endpoint, keeps the combustion investigations, saves each record and file tree to `data/raw/<psi_id>/record.json` and `files.json`, and writes `pipeline/inventory.csv`. Run with `python -m pipeline.run_all inventory`; add `--refresh` to re-download.
- Columns: `psi_id, acronym, title, project_type, platform, hardware, sub_area, doi, related, version, n_tables, table_files, n_docs, docs_mb, n_publications, total_gb, file_categories, hardware_family, notes, url`.
- `hardware_family` starts from the families in the challenge brief (`acme_gas_flames`, `mdca_droplets`, `msg_flow_duct`, `saffire`, `smoke_aerosol`, `ground_study`) and `notes` is free text. Both can be edited by hand in the CSV; a re-run keeps the edits.
- Long timeout and retries: PSI-23 and PSI-39 are multi-megabyte records that take over a minute.

**Gate:**
- [ ] `inventory.csv` has 24 rows and every `psi_id` opens on the PSI site
- [ ] For each investigation we know its tables, documents and their sizes
- [ ] Investigations with no documents are listed, with where their O₂ / pressure / flow ranges will come from instead
- [ ] Test-level priority confirmed: flow-duct family (BASS, BASS-II, SPICE, SLICE) unless the inventory says otherwise
- [ ] PR #0 merged (skeleton + inventory)

---

## Phase 2: Data contract (Sat 14:15–15:00)

The dashboard already defines the contract: `docs/data-contract.md` and `app/src/types.ts` on the `app` branch (two files, `investigations.json` and `tests.json`). The pipeline follows it; it does not define a second schema.

**Build:**
- `models.py`: Pydantic models that mirror the contract field for field (`Investigation`, `TestRow`, and the two file wrappers). Unknown fields are rejected, so a typo cannot slip through.
- Optional pipeline extras the dashboard may ignore:
  - `Investigation`: `hardware_family`, `sub_area`, `objectives`, `earth_benefits`, `n_publications`
  - `TestRow`: `extra`, a small dictionary for values only some families report (for example `burning_rate_mm2_s`, `oxidizer_o2_pct`, `flow_direction`)
- `data/schema.json`, exported with `python -m pipeline.models`. A test fails if it goes stale.
- `validate.py` (`python -m pipeline.run_all validate [folder]`). Errors:
  - a row that breaks the contract, a duplicate id, a test pointing at an unknown investigation
  - O₂ outside 0–100 %, pressure outside 0–1000 kPa, flow outside 0–1000 cm/s
  - a real row (`sample: false`) with no source file and row
- No `data/sample.json`: the dashboard has its own sample data (`app/scripts/make-sample-data.mjs`), and it passes `validate`.

**Gate:**
- [ ] `pytest pipeline -q` passes
- [ ] `validate` passes on the dashboard's sample data from the `app` branch
- [ ] CompE #2 agrees to the optional extras (they change nothing the dashboard reads today)

**Decided at this gate (Sat 14 Nov):**
1. **Test-level order:** FLEX and CFI first (oxygen and pressure are in the table), then BASS-II and Saffire (oxygen only; pressure is assumed to be cabin pressure and every such row says so in `note`), then the ACME tables. The flow-duct tables other than BASS-II are too thin to lead with: BASS lists only materials, SPICE has fuel and air rate, SLICE has two columns. Whether an assumed pressure may appear on the atmosphere map is the ME teammate's call.
2. **Ranking lives in the dashboard only** (`app/src/scoring.ts`, adjustable weights, no model). The pipeline does not build `score.py` or `rankings.json`.
3. **Output stays in `data/out/`.** The pipeline never writes into `app/`. CompE #2 copies the two files into `app/public/data/`, ideally with one line in the app's build script so it cannot be forgotten.
4. **Inverse flames (ACME):** the oxygen comes out of the burner, so `o2_pct` (ambient oxygen) stays empty and the value goes in `extra.oxidizer_o2_pct`. Those rows stay off the atmosphere map.

---

## Phase 3: Fetch and text extraction (Sat 15:00–17:00)

**Build:**
- `fetch.py`: reads each `record.json`, asks the download endpoint for a signed URL per file, downloads into `data/raw/<psi_id>/` and writes `data/raw/manifest.json` (file name, version, sha256, bytes, fetched_at). Safe to re-run.
- Downloads experimental-table CSVs into `data/raw/<psi_id>/tables/` and PDF, Word and PowerPoint files from Reports, Science Documents, Engineering Documents and Presentations into `data/raw/<psi_id>/docs/`: 331 files, 897 MB. **Never `Raw Data`** (ACME Flame Design and CFI-G are about 2 TB each). 60 MB per-file cap as a second guard.
- Not downloaded: 13 Excel files, zips and videos. `fetch --dry-run` lists the counts.
- A file PSI lists but cannot serve (HTTP 404) is recorded under `unavailable` in the manifest and does not fail the run; a network error does.
- Signed URLs expire and are never stored; the manifest keeps the file name and version instead.
- `extract_text.py`: turns each PDF into `data/interim/text/<file name>.jsonl`, one line per page `{source_id, psi_id, category, page, text}`, where `source_id` is the PSI file name. Runs the same on Windows, macOS and Linux:
  - `.pdf`: PyMuPDF (falls back to the `pdftotext` command if PyMuPDF is missing)
  - `.pptx`: read directly in Python; the page number is the slide number
  - `.docx`: read directly in Python; Word files have no fixed pages, so the text is cut into parts and cited as "part 3"
  - `.doc`, `.ppt` (15 files): the old formats need LibreOffice; without it they are skipped and listed as unreadable
- Files are saved under names every system accepts (no `:` or `/`, at most 150 characters); the manifest keeps the original PSI name as `file_name`.
- Each PSI record also becomes `<psi_id>_record.jsonl`, one line per section (objectives, approach, hypothesis, Earth benefits, publication list). Publication abstracts are not collected.
- `index.json` lists every source with page and character counts; PDFs with almost no text layer are reported as scanned (no OCR).

**Gate:**
- [ ] Every inventory row that has files shows up in the manifest; nothing over the size limit was downloaded
- [ ] The text from 3 random pages, checked by eye, matches the PDF
- [ ] Re-running `fetch` downloads nothing new

---

## Phase 4: investigations.json (Sat 16:00–16:30)

**Build:**
- `extract_investigations.py` writes `data/out/investigations.json`, one record per investigation in the contract shape:
  - **Copied from the PSI record, no AI:** id, acronym, title, flight or ground, hardware, related investigations, DOI, URL, plus the extras (sub-area, objectives, Earth benefits, publication count, hardware family).
  - **Proposed by the AI, checked by the ME teammate:** `role` (prevention, detection, suppression, fundamentals) and `fuel_phase` (solid, liquid, gas, other). For each tag the AI must quote the sentence it relied on. The quote is checked against the record text; one retry, then the tag is flagged.
- The contract has no oxygen, pressure or flow fields per investigation, so nothing else is extracted here. Those ranges come from the test rows in Phase 5.
- AI tags are saved in `pipeline/review/investigation_tags.json` (committed), so a rebuild on another machine needs no AI calls.
- `review-export` writes `pipeline/review/investigations.csv`: one row per tag with the AI value and its quote. The ME teammate types `y` under `agree` or a value under `corrected_value`.
- `apply-overrides` turns the reviewed sheet into `data/overrides.json` and rebuilds. Overrides are applied last on every build. `meta.roles_reviewed` becomes true once every row is reviewed.

**Gate:**
- [ ] `validate` passes on `data/out/`
- [ ] 0 tags whose quote is missing from the record
- [ ] I check 3 investigations against their PSI pages
- [ ] The ME teammate has the review sheet (review can continue alongside Phase 5)

---

## Phase 5: Test level, FLEX and CFI first (Sat 20:00–00:30)

**Build:**
- `propose_mapping`: the AI reads each table's header and first 5 rows and writes `mappings/<table>.yaml` (column → field, unit conversion, how to read the results). **I review and edit every YAML file.**
- `transform_tests.py`: plain code applies the mappings and writes `tests.json`, one row per test in the contract shape, with family-specific results in `extra`. Every row keeps its `source` file and row.
- Order: FLEX, CFI, BASS-II, Saffire I–II, then the ACME tables.
- Sanity ranges in `validate.py`:
  - O₂ between 0 and 100%
  - pressure between 0 and 1000 kPa
  - flow at least 0
  - every key in `extra` is in the allowed list for its family

**Gate:**
- [ ] Row count per table equals the source table's row count (minus documented skips)
- [ ] 5 random output rows traced back by hand to the raw file
- [ ] **End-to-end check with CompE #2:** 2 real investigations show on the atmosphere map at the deployed URL
- [ ] PR #3 merged

*Sleep 01:00–07:00.*

---

## Phase 6: Derived data (Sun 07:00–10:00)

**Build:**
- No ranking here: the dashboard scores investigations itself (decided at the Phase 2 gate).
- `coverage.py` writes `coverage.json`: a grid of fuel phase × O₂ / pressure band × gravity, counting tests per cell. Empty cells are the gaps.
- `varied.py` writes `varied.json`: for each test-level investigation, which conditions changed and which stayed constant.

**Gate:**
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
| Sat ~00:30 | CompE #2 | End-to-end check on the deployed URL |
