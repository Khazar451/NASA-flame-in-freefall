# Dashboard

React + Vite + Plotly front end for the Flame in Freefall challenge.

## Run it

Needs Node 20 or newer.

```
cd app
npm install
npm run dev
```

Open the address Vite prints (normally http://localhost:5173).

`npm run build` writes the static site to `app/dist/`. It uses relative paths, so it can be hosted from any folder.

## Deploy

`.github/workflows/deploy.yml` builds the dashboard and publishes it to GitHub Pages on every push to `main` that changes `app/` or `data/out/`. It can also be started by hand from the Actions tab.

- Address: https://khazar451.github.io/NASA-flame-in-freefall/
- One-time setup by the repo owner: Settings > Pages > Source: "GitHub Actions".
- The Ask tab calls the Q&A server at https://nasa-flame-in-freefall.onrender.com; the address is set in the workflow file. To use another server without a commit, add a repository variable named `VITE_API_URL` (Settings > Secrets and variables > Actions > Variables) and re-run the workflow.

## What is here

| View | File | What it shows |
|---|---|---|
| Atmosphere map | `src/views/AtmosphereMap.tsx` | Tests by oxygen level and pressure, grouped into marks sized by test count, with the cabin, lunar base and spacesuit atmospheres and NASA's planned 8.2 psi / 34 % exploration atmosphere marked. Filters by role and by air-flow regime; a toggle zooms from the full oxygen scale to the tested range |
| Ranking | `src/views/Ranking.tsx` | Investigations ranked for a chosen habitat, with the score broken into its four parts. Hovering a name shows a short summary; selecting it opens the full record |
| Test results | `src/views/Results.tsx` | Measured results that the map cannot show: droplet size at ignition against size at extinction (FLEX), and burn time by fuel inside each experiment |
| Ask | `src/views/Ask.tsx` | Question box wired to the Q&A backend |

Other files:

- `src/habitats.ts`: habitat atmospheres (from the challenge brief; the lunar range is capped at 36 % oxygen) and the scoring settings, including `FUEL_OVERRIDES` (ACME BRE's gas burner emulates a solid, so its fuel counts 0.85). **The fuel and role values and the default weights are a draft for the domain lead to review.**
- `src/scoring.ts`: the score calculation. No model is involved.
- `src/topics.ts`: topics that cut across the four roles. The "Cool flames" filter on the map and the ranking lists the investigations whose NASA record names cool flames in its title, objectives or research impacts.
- `src/types.ts`: the shape of the data files. See `docs/data-contract.md`.
- `src/components/Plot.tsx`: small wrapper around Plotly.
- `src/styles.css` and `src/chartTheme.ts`: the look of the site, styled as a flight data sheet: warm paper, ruled panels, IBM Plex Serif for headings and IBM Plex Mono for labels and figures. Chart colours are repeated in `chartTheme.ts` because Plotly cannot read CSS variables; change both together.
- `src/components/InvestigationDetail.tsx`: the panel shown when a mark on the map is selected: the investigation record, a summary of its tests, and its objectives.

## Data

The app reads two files from `public/data/`:

- `investigations.json`
- `tests.json`

Both come from the pipeline, which writes them to `data/out/` at the repo root. `npm run dev` and `npm run build` first run `npm run sync-data` (`scripts/sync-data.mjs`), which copies them into `public/data/`. That folder is not in git: `data/out/` is the only copy that is. After pulling new pipeline output, restart `npm run dev` to pick it up.

To work without the pipeline output, `npm run sample-data` writes placeholder files straight into `public/data/`. Only five of those rows are real; the rest are randomly generated, and the app shows a yellow banner while any row has `"sample": true`.

## Q&A backend

Copy `.env.example` to `.env` and set `VITE_API_URL` to the backend address, then restart `npm run dev`.

The front end sends:

```
POST {VITE_API_URL}/ask
{ "question": "..." }
```

and expects:

```
{ "answer": "...", "citations": [{ "label": "FLEX (PSI-69)", "url": "https://doi.org/10.60555/mbq8-0451" }] }
```

The backend must allow cross-origin requests from the dashboard's address.

## What the real data does not have

Checked against the pipeline output of 15 Nov, as of pipeline PR #26 (24 investigations, 1,828 tests). The dashboard counts these from the data files, so the screens stay right when the data changes; only this list has to be updated by hand.

- 1,553 tests have both oxygen and pressure. Only those are drawn on the atmosphere map and matched to a habitat in the ranking. The other 275 still count toward "Amount of data"; 274 of them are inverse flames, which have no ambient oxygen level.
- 812 of the drawn tests use an assumed cabin pressure, because their source table does not give one: BASS, BASS-II, SPICE, Saffire-I, II and III, SAME and SAME-R. For 672 of them (BASS, SPICE, Saffire-III, SAME and SAME-R) oxygen is assumed too, at 21 %. The hover text and the investigation panel say which values are assumed, read from the `assumed` list on each test row.
- SAME and SAME-R are smoke tests: a material is heated to make smoke and there is no flame. The dashboard says so in place of a flame outcome. It recognises them by `hardware_family: "smoke_aerosol"` on the investigation.
- 1,159 of the drawn tests have no recorded outcome, the 113 smoke tests among them. The hover text on their mark says so; they are never counted as "kept burning".
- Eleven investigations have no per-test data of their own. Five of them are ground studies of flight data, and the ranking scores them on the tests of the flight experiments they analyse (`dataSources` in `src/scoring.ts`); the other six score on fuel type and role only.
- Three ACME BRE rows are not tests. Its table gives no pressures, so the pipeline writes one row per atmosphere that its final report describes. The dashboard draws them, labels them "report summary" and leaves them out of the test counts in the lunar coverage panel. It recognises them by `extra.row_kind: "atmosphere"` on the row.
- 17 FLEX tests at 30 to 34 % oxygen ran at 10.3 to 10.5 psi (0.7 atmospheres as measured), just over the 10.2 psi limit of the lunar range. They are not counted as inside it; the coverage panel names them on a separate line (`EDGE_NOTE_PSI` in `src/habitats.ts`). The domain lead chose this over widening the range.
- 651 tests have an air-flow speed. The map's air-flow filter puts the 0 cm/s tests, and ACME and droplet tests (sealed chambers with no forced flow), under "No forced flow", and keeps "Flow not recorded" for the rest (`src/flow.ts`). For BASS-II the source lists several speeds for one burn (for example "5 3 2 1"); the pipeline keeps the first and says so in the note.
