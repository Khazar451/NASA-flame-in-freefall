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
| Atmosphere map | `src/views/AtmosphereMap.tsx` | Tests by oxygen level and pressure, grouped into marks sized by test count, with the cabin, lunar base and spacesuit atmospheres marked. A toggle zooms from the full oxygen scale to the tested range |
| Ranking | `src/views/Ranking.tsx` | Investigations ranked for a chosen habitat, with the score broken into its four parts. Hovering a name shows a short summary; selecting it opens the full record |
| Ask | `src/views/Ask.tsx` | Question box wired to the Q&A backend |

Other files:

- `src/habitats.ts`: habitat atmospheres (from the challenge brief) and the scoring settings. **The fuel and role values and the default weights are a draft for the domain lead to review.**
- `src/scoring.ts`: the score calculation. No model is involved.
- `src/types.ts`: the shape of the data files. See `docs/data-contract.md`.
- `src/components/Plot.tsx`: small wrapper around Plotly.
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

Checked against the pipeline output of 14 Nov, as of pipeline PR #15 (24 investigations, 1,828 tests). The dashboard counts these from the data files, so the screens stay right when the data changes; only this list has to be updated by hand.

- 881 tests give both oxygen and pressure. Only those are drawn on the atmosphere map and matched to a habitat in the ranking. The other 947 still count toward "Amount of data".
- 140 of the drawn tests (BASS-II, Saffire) use an assumed cabin pressure, because their source table gives oxygen only. The hover note on each says so.
- 487 of the drawn tests have no recorded outcome. The hover text on their mark says so; they are never counted as "kept burning".
- Eleven investigations have no per-test data at all and score on fuel type and role only.
- 651 tests have an air-flow speed. For BASS-II the source lists several speeds for one burn (for example "5 3 2 1"); the pipeline keeps the first and says so in the note.
