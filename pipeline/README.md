# pipeline/

Data and AI pipeline: PSI downloads (`data/raw/`) to the static files the dashboard reads (`data/out/`), plus the `/ask` endpoint. Plan and phase gates: [implementation_plan.md](implementation_plan.md).

## Setup

```bash
python3 -m venv pipeline/.venv && source pipeline/.venv/bin/activate
pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env   # then add GEMINI_API_KEY and LLM_MODEL
```

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
