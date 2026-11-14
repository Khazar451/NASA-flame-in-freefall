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

## Phase 5: tests.json (Sat 16:30–17:15)

**Build:**
- One mapping file per table in `pipeline/mappings/*.yaml`: which column feeds which field, the unit conversion, which cell texts mean "no value", which rows to skip, and how the outcome column is read. Written by hand from the tables' real contents; no AI.
- `transform_tests.py` applies the mappings with plain code and writes `data/out/tests.json`, one row per test in the contract shape, with family-specific values in `extra`. Every row keeps its source file and row.
- A cell the mapping cannot read fails the build and is listed, so a wrong mapping cannot silently produce empty values.
- A source value that cannot be real (a pressure of 0, a negative flow) is left empty and the row's note quotes it.
- `pipeline/mappings/_skipped.yaml` lists the 8 tables that are not test-level, with the reason for each.

**Result:** 1,828 rows from 14 tables and one report, covering 13 investigations; 881 rows have both oxygen and pressure (after the review fixes below).

**For the ME teammate to check (each is marked TO CHECK in its mapping file):**
- FLEX: "Completion" is read as sustained, "Disruption" as unknown.
- CFI-G: "SE" is read as extinguished, "FT" (flow terminated) as sustained, "-" as no ignition.
- BASS-II and Saffire-I/II: cabin pressure (1 atm) is assumed, and every such row says so.
- Inverse flames (Flame Design, CFI-G) have no ambient oxygen; the burner's oxygen is in `extra.oxidizer_o2_pct`.

**Gate:**
- [ ] Row count per table equals the source table's row count minus the skipped rows the build lists
- [ ] 5 random output rows traced back by hand to the raw file
- [ ] `validate` passes on `data/out/`
- [ ] **End-to-end check with CompE #2:** the real files load in the dashboard

*Sleep 01:00–07:00.*

---

## Phase 6: Gap map and "what varied" (Sat 16:45–17:00)

No ranking here: the dashboard scores investigations itself (decided at the Phase 2 gate). No AI is used.

**Build:**
- `coverage.py` writes `data/out/coverage.json`: every test with oxygen and pressure goes into one cell of fuel phase (solid, liquid, gas) × 5 oxygen bands × 5 pressure bands. All 75 cells are written; an empty cell is a gap.
  - Band edges follow `app/src/habitats.ts`, so the lunar range (30 % oxygen or more, 4.3–10.2 psi) is exactly two cells per fuel, and each cell lists the habitats inside it.
  - Tests that cannot go on the grid are counted under `not_on_grid` with the reason (inverse flame, no oxygen, no pressure).
  - Gravity is not a dimension: every PSI test ran in microgravity. `meta.gravity` says so.
- `varied.py` writes `data/out/varied.json`: for each investigation, each test condition marked varied, constant or not recorded, with its range and levels, plus a one-paragraph summary. Numbers are compared after rounding (oxygen to 1 %, pressure to 5 kPa), so measurement noise is not called variation. Assumed values (cabin pressure) are labelled assumed, never constant.
- Both files have Pydantic models in `models.py` and appear in `data/schema.json`. `validate` checks that they match `tests.json`.

**Result:** 881 tests on the grid, 51 of 75 cells empty. Inside the lunar range: 21 gas-flame tests (20 ACME CFI-G and one ACME BRE atmosphere), no liquids, no solids. Nothing at spacesuit conditions.

**Gate:**
- [ ] The empty cells match the gaps the ME teammate expects
- [ ] The "what varied" summaries read correctly for 3 investigations
- [ ] `validate` passes on `data/out/`

---

## Phase 7: `/ask` (Sat 17:00–19:00)

Decided with Khazar: **retrieval first**, **ChromaDB embeddings** for document search, **the dashboard teammate deploys** the server. The request and reply shape is the one in `app/README.md` and `app/src/views/Ask.tsx`.

**Build:**
- `build_index.py` (`build-index`): every readable page or record section → chunks of at most 1,500 characters with 200 overlap; scanned, unreadable and near-empty pages skipped. Embedded locally with Chroma's default model (all-MiniLM-L6-v2, no AI quota). Saved to `data/out/search/` (chunks, float16 vectors, manifest) and committed, so the server needs no raw data.
- `search.py`: loads that index into an in-memory Chroma collection; questions are embedded on demand.
- `ask.py`: the answer, in this order:
  - demo questions → the stored answer in `pipeline/review/demo_answers.json`;
  - partial-gravity questions → a fixed "no data" answer with the bounds (microgravity and 1 g), E-FIELD as stand-in, LUCI and FM2 to come;
  - otherwise the **retrieval answer**: plain rules read the question (experiment names, fuel, oxygen and pressure limits in %, psi, kPa, atm, mmHg, habitat words, CO₂/helium), filter `tests.json` and summarise it; when nothing matches, the nearest real limit is given. Plus the closest document passages, quoted with their source;
  - when the AI budget allows, Gemini rewrites that from the numbered sources only. The AI answer is dropped if any number in it is not in the sources (or a unit conversion of one).
- `api.py`: FastAPI. `POST /ask`, `GET /health`, CORS, 500-character limit, per-address rate limit, daily AI budget. Reply `{answer, citations: [{label, url, kind, page?, snippet?}], mode, note?}`; citation URLs are unique.
- `demo-answers`: answers the 10 demo questions once and saves them.
- `requirements-api.txt`: the server's own, smaller requirements.

**Gate:**
- [ ] `pytest pipeline -q` passes
- [ ] The server runs locally and answers the 10 demo questions
- [ ] Every number in the demo answers matches `tests.json` or a quoted passage
- [ ] The partial-gravity question gets the no-data answer
- [ ] With AI off, every question still gets a retrieval answer
- [ ] The dashboard's Ask view works against the local server (with CompE #2)

---

## Phase 8: Rebuild check, extras, then freeze (Sat 18:00 onward; freeze Sun 18:00)

**Rebuild check (done Sat 14 Nov):**
- Exact versions pinned in `requirements.txt` and `requirements-api.txt`.
- `run_all all` leaves out the hand-run steps (`review-export`, `apply-overrides`, `demo-answers`): they involve a person or spend AI quota.
- The search manifest no longer records a build date, so the same inputs give the same bytes.
- Fresh clone of `pipeline` + the downloaded files in `data/raw/` → new venv → `pip install -r pipeline/requirements.txt` → `python -m pipeline.run_all all` (about 6 minutes) → every file in `data/out/` and `inventory.csv` identical to the committed ones; 183 tests pass. No AI call was needed.
- Not covered: downloading `data/raw/` again from PSI (about 900 MB; `fetch` was checked in Phase 3).

**Extras, only if wanted (each needs a dashboard view from CompE #2):**
1. **Done:** `gravity.py` (`gravity` step) writes `data/out/gravity.json` for a partial-gravity panel, all by plain code:
   - `levels`: microgravity (all test rows), Moon and Mars (no tests), Earth (comparison only);
   - `stand_in`: ACME E-FIELD, with two quotes from its PSI record and the reference it cites (Karnani et al., *Simulating Gravity in Microgravity Combustion Using Electric Fields*, 2012);
   - `paired_results`: Saffire-II's microgravity and normal-gravity burn lengths and spread rates for the same 9 samples, with the table's footnotes and a counted summary (4 silicone samples barely burned in microgravity; 3 of them burned at 1 g);
   - `statements`: 27 sentences from PSI records that compare gravity levels, word for word;
   - `upcoming`: LUCI and FM2, cited to the challenge brief only (no PSI document mentions them).
   `/ask`'s partial-gravity answer now adds the Saffire-II comparison. `validate` checks the file against `tests.json`.
2. Flame-out prediction from the FLEX rows (oxygen, pressure, diluent → extinguished or not), only if the ME teammate judges it sound.
3. Hardware and data-reuse graph from `related` and `hardware_family` in `investigations.json`.

**Gate (Sun 18:00 data freeze):**
- [x] Fresh rebuild gives the same files
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

---

## Fixes after the data review (Sat 14 Nov, evening)

A review against NASA's tables and reports found three errors. Fixed without adding any new assumption:

1. **Exact duplicate rows counted twice.** CFI repeats 8 rows and SPICE 34, identical down to their timestamps. `transform_tests.py` now drops an exact copy of an earlier row and reports how many (1,867 → 1,825 table rows).
2. **BASS-II flow speed was discarded.** The BASS-II Summary Report (p. 74) says the air velocity was "read out to the units place … ±0.5 cm/s", so "Air display" is the flow speed. 118 of 129 rows now have it (0–20 cm/s). When a cell lists several readings because the flow was changed during the test, the first is used and the note quotes the cell. Cells with no starting number, or with the fan setting mixed in, stay empty.
3. **ACME BRE was missing.** Its table has no pressures, but its final report states the atmospheres it tested (p. 5) and the result (p. 18: flames mostly went out at 21 % / 14.7 psia and "generally persisted for 3 minutes or more" at 27 % / 10.2 psia and 34 % / 8.2 psia). These are 3 atmosphere-level rows (`mappings/bre_atmospheres.yaml`, `kind: document`); the build checks every number against the quoted page. One of them (34 % at 8.2 psia) lies inside the lunar range.

Also: the `/ask` provider chain moves to NVIDIA when Gemini is overloaded (HTTP 503), and AI sources are labelled with the experiment's name, not only its PSI id (SAME's documents are filed under SAME-R). The demo answers were rebuilt on the fixed data.

### Answer quality (same evening)

Live answers were accurate but poor: they opened with test counts, and search missed NASA's stated results (for example BRE's pressure result). Four changes, measured on `pipeline/review/eval_questions.yaml` (15 questions, 34 facts, each from NASA's data or reports; `python -m pipeline.evaluate --provider nim`):

1. `findings.py` (`findings`, hand-run, AI): for each experiment, the AI reads the pages most likely to state results and writes 3–6 findings, each with a word-for-word quote. Code checks the quote is on the page and every number in the finding is in the quote. 66 findings from 20 experiments in `data/out/findings.json`; searched first by `/ask`.
2. Search: `keyword_index.py` adds BM25 keyword search (2.3 MB, `data/out/search/keywords.npz`) merged with the meaning-based search; at most 2 passages per document; planning documents and reference lists ranked last; a short synonym list (pressure → psia, atm, kPa).
3. Instructions: answer the question in the first sentence; findings first; the test table is authoritative for conditions; counts only when asked.
4. A code check rejects an answer that ignores the experiments the test table lists (it had answered "no tests above 2 atm" while the table lists CFI-G and Flame Design).

Result with the NVIDIA model: facts in the answers 22/34 → 29/34; facts in the sources given to the AI 27/34 → 33/34; no answer opens with a test count. Server memory after the changes: about 435 MB after startup, 450–460 MB under load (freed memory is handed back after loading; at most 2 questions are answered at once). Remaining misses are mostly the NVIDIA model preferring document values over the table; Gemini, the main model, follows the table more closely.

SAME and SAME-R: their 113 tests now sit at the cabin point (21 % oxygen, 14.7 psi), labelled as assumed, as agreed with the ME teammate.

### Explained answers for every kind of question (2026-11-15)

Live answers to plain questions ("so what is BASS") still opened with the test count, named the test-table summary and listed hardware from acceptance reviews. Changes, for all questions:

1. Each experiment the question names gets an overview from its PSI record (what it is, objectives, why it matters) as the first source; the no-AI answer opens with it too.
2. New instructions: answer first, then why or how and what was found, 3 to 6 sentences of prose; no counts, hardware lists, document titles, source talk or PSI ids unless asked. `tidy()` removes an unasked count opener, "test-table summary" and "(PSI-nn)" if they slip through.
3. Fixes that had thrown good answers away: the check that an answer names the listed experiments misread conditions holding a colon (lunar habitat) and now applies only to answers that deny something; family names count ("Saffire"); numbers written as words count ("Fifty-two"); PSI ids no longer fail the number check.
4. A Gemini timeout now goes to NVIDIA instead of the no-AI answer. Hardware and acceptance reviews rank with planning documents.

On 20 questions (5 new plain "what is" ones; `eval_questions.yaml`): Gemini first, NVIDIA as fallback, 43/47 facts, every answer written by AI, 2–4 s each. NVIDIA alone: 35/47, often one sentence, and once moved a result to the wrong experiment, so Gemini stays first. Demo answers rebuilt.

Still open, for the ME teammate: the outcome mappings marked TO CHECK, the AI role and fuel tags, and whether the existing cabin-pressure assumption for BASS-II and Saffire may stay on the map.

