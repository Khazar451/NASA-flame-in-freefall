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

## What is here

| View | File | What it shows |
|---|---|---|
| Atmosphere map | `src/views/AtmosphereMap.tsx` | Every test by oxygen level and pressure, with the cabin, lunar base and spacesuit atmospheres marked |
| Ranking | `src/views/Ranking.tsx` | Investigations ranked for a chosen habitat, with the score broken into its four parts |
| Ask | `src/views/Ask.tsx` | Question box wired to the Q&A backend |

Other files:

- `src/habitats.ts`: habitat atmospheres (from the challenge brief) and the scoring settings. **The fuel and role values and the default weights are a draft for the domain lead to review.**
- `src/scoring.ts`: the score calculation. No model is involved.
- `src/types.ts`: the shape of the data files. See `docs/data-contract.md`.
- `src/components/Plot.tsx`: small wrapper around Plotly.

## Data

The app reads two files from `public/data/`:

- `investigations.json`
- `tests.json`

**The current `tests.json` is placeholder data.** Only five BASS-II rows are copied from the PSI experimental table; the other 265 are randomly generated so the views have something to draw. The app shows a yellow banner while any row has `"sample": true`. Replace both files with the pipeline output and the banner disappears.

`npm run sample-data` regenerates the placeholder files.

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

## Not checked yet

This was written without access to npm, so `npm install` and the real Plotly chart have not been run. The app logic was tested in a browser with a stand-in for Plotly. If the chart misbehaves on first run, start in `src/views/AtmosphereMap.tsx` (traces and layout) and `src/components/Plot.tsx`.
