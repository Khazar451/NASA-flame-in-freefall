"""Paths and environment settings shared by every pipeline step."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # downloads, gitignored
INTERIM_DIR = DATA_DIR / "interim"  # extracted text, caches, review sheets, gitignored
OUT_DIR = DATA_DIR / "out"          # static files the dashboard reads, committed

ENV_FILE = PIPELINE_DIR / ".env"    # gitignored; copy from .env.example


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # dotenv is optional; real env vars still work
        return
    load_dotenv(ENV_FILE, override=False)


_load_env_file()


def env(name: str, default: str | None = None) -> str | None:
    """Read a setting at call time, treating empty strings as unset."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def llm_cache_dir() -> Path:
    return Path(env("LLM_CACHE_DIR") or INTERIM_DIR / "llm_cache")
