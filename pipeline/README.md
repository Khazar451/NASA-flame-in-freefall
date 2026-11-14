# pipeline/

Data and AI pipeline: PSI downloads (`data/raw/`) to the static files the dashboard reads (`data/out/`), plus the `/ask` endpoint. Plan and phase gates: [implementation_plan.md](implementation_plan.md).

## Setup

```bash
python3 -m venv pipeline/.venv && source pipeline/.venv/bin/activate
pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env   # then add GEMINI_API_KEY and LLM_MODEL
```

On Windows (PowerShell):

```powershell
py -m venv pipeline\.venv; pipeline\.venv\Scripts\Activate.ps1
pip install -r pipeline\requirements.txt
copy pipeline\.env.example pipeline\.env
```

Everything is plain Python and runs the same on Windows, macOS and Linux. One optional
extra: 15 source documents are in the old `.doc` / `.ppt` formats, which only LibreOffice
can read. Without LibreOffice installed those 15 are skipped and listed as unreadable;
nothing else changes.

## Commands (run from the repo root)

```bash
python -m pipeline.llm --check      # one tiny live call: are the key and model OK?
python -m pipeline.run_all --help   # list steps
python -m pipeline.run_all all      # rebuild data/out/ from data/raw/
pytest pipeline                     # tests (no network, no API key needed)
```

## Rules

- `data/raw/` and `data/interim/` are never committed.
- Every value in `data/out/` records its source (`source_id` plus page or row).
- All AI calls go through `pipeline/llm.py`, which caches replies in `data/interim/llm_cache/`.

## The /ask server (for whoever deploys it)

```bash
pip install -r pipeline/requirements-api.txt
python -m pipeline.search --download-model   # optional: fetch the 80 MB model now
uvicorn pipeline.api:app --host 0.0.0.0 --port $PORT
```

Run it from the repository root. It reads the committed files in `data/out/` (including `data/out/search/`) and `pipeline/review/demo_answers.json`; it does not need `data/raw/`. Memory use peaks at about 380 MB. `GET /health` reports what loaded and how many AI calls are left today.

| Variable | Default | Meaning |
|---|---|---|
| `ASK_ALLOWED_ORIGINS` | `*` | Comma-separated addresses allowed to call the API (the dashboard's URL) |
| `LLM_PROVIDER`, `LLM_MODEL`, `GEMINI_API_KEY` | unset | As in `.env.example`. Unset means answers without AI |
| `LLM_DAILY_LIMIT` | `60` | AI calls per day; after that, answers without AI |
| `ASK_RATE_PER_MINUTE` | `10` | Questions per minute from one address |
| `EMBEDDING_MODEL_DIR` | `~/.cache/chroma` | Where the embedding model is kept; relative paths start at the repository root |

**The Gemini key is a server secret.** Put it only in the host's environment settings, never in `app/.env`, a `VITE_` variable or the repository: the dashboard is public, and anything built into it can be read by anyone.

### Deploy on Render

`render.yaml` at the repository root holds every setting.

1. Render: **New > Blueprint**, pick this repository, branch `main`. Render reads `render.yaml`.
2. When asked for `GEMINI_API_KEY`, paste the key. Leave it empty to run without AI.
3. Wait for the deploy to show **Live**, then open `https://<service>.onrender.com/health`.
4. GitHub: **Settings > Secrets and variables > Actions > Variables**, set `VITE_API_URL` to `https://<service>.onrender.com` (no trailing slash).
5. GitHub: **Actions > Deploy dashboard > Run workflow**, so the site is rebuilt with that address.

A service created by hand instead of from the Blueprint needs the same settings: root directory empty, the build and start commands and the environment variables from `render.yaml`.

On the free plan the service sleeps after about 15 minutes without requests; the next question wakes it, which takes about a minute.
