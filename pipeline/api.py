"""Phase 7: the /ask web service the dashboard calls.

    uvicorn pipeline.api:app --host 0.0.0.0 --port 8000

POST /ask   {"question": "..."}  ->  {"answer", "citations": [{"label", "url", ...}], "mode"}
GET  /health                     ->  what is loaded and how much AI budget is left
GET  /                           ->  a short note saying the service runs and where to ask

Settings (environment variables or pipeline/.env):
  ASK_ALLOWED_ORIGINS   comma-separated origins allowed to call the API (default "*")
  LLM_DAILY_LIMIT       AI calls per day before answers are retrieval-only (default 60)
  ASK_RATE_PER_MINUTE   questions per minute from one address (default 10)
  LLM_PROVIDER, LLM_MODEL, GEMINI_API_KEY   as in pipeline/llm.py; unset = retrieval only
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from pipeline import ask, config, llm

log = logging.getLogger("pipeline.api")


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Citation(BaseModel):
    label: str
    url: str
    kind: str | None = None
    page: int | None = None
    snippet: str | None = None


class Reply(BaseModel):
    answer: str
    citations: list[Citation]
    mode: str
    note: str | None = None


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def _provider() -> llm.Provider | None:
    try:
        return llm.get_provider()
    except llm.LLMError as exc:
        log.warning("AI answers are off: %s", exc)
        return None


def create_app(kb: ask.KnowledgeBase | None = None, provider: llm.Provider | None = None,
               use_ai: bool = True) -> FastAPI:
    """The app. Tests pass their own knowledge base and provider; the server loads the real ones."""
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if kb is None:
            from pipeline.search import SearchIndex

            state["kb"] = ask.KnowledgeBase.load(search=SearchIndex.load())
        else:
            state["kb"] = kb
        state["provider"] = provider if provider is not None else (_provider() if use_ai else None)
        state["budget"] = ask.Budget(int(config.env("LLM_DAILY_LIMIT") or 60))
        state["limiter"] = RateLimiter(int(config.env("ASK_RATE_PER_MINUTE") or 10))
        yield

    app = FastAPI(title="Flame in Freefall Q&A", lifespan=lifespan)
    origins = [o.strip() for o in (config.env("ASK_ALLOWED_ORIGINS") or "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    @app.get("/")
    def index() -> dict[str, Any]:
        # Opening the bare address in a browser used to show {"detail": "Not Found"},
        # which looked like a broken deploy.
        return {
            "service": "Flame in Freefall Q&A",
            "status": "running",
            "ask": "POST /ask with JSON {\"question\": \"...\"}",
            "health": "/health",
            "dashboard": "https://khazar451.github.io/NASA-flame-in-freefall/#ask",
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        knowledge, budget = state["kb"], state["budget"]
        return {
            "status": "ok",
            "investigations": len(knowledge.investigations),
            "tests": len(knowledge.tests),
            "search": knowledge.search is not None,
            "demo_answers": len(knowledge.demo),
            "ai": state["provider"] is not None and not budget.exhausted,
            "ai_calls_left_today": max(0, budget.limit - budget.used) if not budget.exhausted else 0,
        }

    @app.post("/ask", response_model=Reply)
    def ask_question(body: Question, request: Request) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        if not state["limiter"].allow(client):
            raise HTTPException(status_code=429, detail="Too many questions; wait a minute and ask again.")
        reply = ask.answer(body.question.strip(), state["kb"], state["provider"], state["budget"])
        seen: set[str] = set()  # the dashboard keys the citation list on url
        reply["citations"] = [c for c in reply["citations"] if not (c["url"] in seen or seen.add(c["url"]))]
        return reply

    return app


app = create_app()
