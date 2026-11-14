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
uvicorn pipeline.api:app --host 0.0.0.0 --port $PORT
```

Run it from the repository root. It reads the committed files in `data/out/` (including `data/out/search/`) and `pipeline/review/demo_answers.json`; it does not need `data/raw/`.

- The first start downloads Chroma's embedding model (about 80 MB) to `~/.cache/chroma`. Memory use is roughly 400–600 MB.
- `GET /health` reports what loaded and how many AI calls are left today.
- Settings, as environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ASK_ALLOWED_ORIGINS` | `*` | Comma-separated addresses allowed to call the API (the dashboard's URL) |
| `LLM_PROVIDER`, `LLM_MODEL`, `GEMINI_API_KEY` | unset | As in `.env.example`. Unset means answers without AI |
| `LLM_DAILY_LIMIT` | `60` | AI calls per day; after that, answers without AI |
| `ASK_RATE_PER_MINUTE` | `10` | Questions per minute from one address |

The dashboard then needs `VITE_API_URL` set to the server's address (see `app/README.md`).
