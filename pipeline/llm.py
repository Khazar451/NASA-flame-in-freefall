"""The only place the pipeline talks to an LLM.

Every call goes through complete_json(), which
  * asks the provider for JSON matching a schema (a Pydantic model or a JSON schema dict),
  * validates the reply (Pydantic models are validated strictly; invalid replies raise),
  * caches the validated reply on disk, keyed by a hash of everything that affects it,
    so re-runs are free and give identical results.

Providers are chosen with LLM_PROVIDER in pipeline/.env:
  gemini  -> Google Gen AI SDK            (pip install google-genai)
  openai  -> any OpenAI-compatible API     (pip install openai); set OPENAI_BASE_URL
             for local servers such as Ollama or LM Studio
  fake    -> offline stub for tests

Check that a key and model work with:  python -m pipeline.llm --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from pipeline import config

CACHE_VERSION = 1  # bump to invalidate every cached reply after a breaking change


class LLMError(RuntimeError):
    """The provider failed or returned something that does not match the schema."""


class QuotaError(LLMError):
    """Every configured model has used up its quota. Waiting a few seconds will not help."""


class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, prompt: str, schema: dict[str, Any], system: str | None) -> str:
        """Return the raw text of a JSON reply that should match `schema`."""
        ...


# --------------------------------------------------------------------------- providers


class FakeProvider:
    """Offline provider for tests. `responder(prompt, schema)` returns a dict or a JSON string."""

    name = "fake"

    def __init__(
        self,
        responder: Callable[[str, dict[str, Any]], Any] | None = None,
        model: str = "fake-1",
    ) -> None:
        self.model = model
        self.calls = 0
        self._responder = responder or (lambda prompt, schema: {})

    def complete_json(self, prompt: str, schema: dict[str, Any], system: str | None) -> str:
        self.calls += 1
        out = self._responder(prompt, schema)
        return out if isinstance(out, str) else json.dumps(out)


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMError("LLM_PROVIDER=gemini needs: pip install google-genai") from exc
        from google.genai import types

        # LLM_MODEL may list several models, e.g. "gemini-3.5-flash,gemini-3.5-flash-lite".
        # The free tier allows about 20 requests a day per model, so when one model's
        # quota runs out the next one in the list is used. `model` (the first) names the
        # configuration in the cache key; `last_model` is the one that actually answered.
        self.models = [m.strip() for m in model.split(",") if m.strip()]
        self.model = self.models[0]
        self.last_model = self.model
        self._exhausted: set[str] = set()
        # Without a timeout a slow model can hang a pipeline run for minutes.
        timeout_ms = int(float(config.env("LLM_TIMEOUT_S") or 90) * 1000)
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms)
        )

    def complete_json(self, prompt: str, schema: dict[str, Any], system: str | None) -> str:
        from google.genai import types

        config_ = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
        )
        for model in self.models:
            if model in self._exhausted:
                continue
            try:
                resp = self._client.models.generate_content(
                    model=model, contents=prompt, config=config_
                )
            except Exception as exc:
                if getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc):
                    self._exhausted.add(model)
                    continue
                raise
            if not resp.text:
                raise LLMError(f"Gemini returned no text (model={model})")
            self.last_model = model
            return resp.text
        raise QuotaError(
            f"quota used up on every configured model ({', '.join(self.models)}). The Gemini "
            "free tier allows about 20 requests per model per day; add more models to "
            "LLM_MODEL or wait for the daily reset."
        )


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, model: str, api_key: str | None, base_url: str | None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError("LLM_PROVIDER=openai needs: pip install openai") from exc
        self.model = model
        # Local servers ignore the key but the SDK requires a non-empty value.
        self._client = OpenAI(api_key=api_key or "not-needed", base_url=base_url)

    def complete_json(self, prompt: str, schema: dict[str, Any], system: str | None) -> str:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema},
            },
        )
        content = resp.choices[0].message.content
        if not content:
            raise LLMError(f"OpenAI-compatible API returned no content (model={self.model})")
        return content


def get_provider() -> Provider:
    """Build the provider named by LLM_PROVIDER from pipeline/.env or the environment."""
    name = (config.env("LLM_PROVIDER") or "").lower()
    if name == "fake":
        return FakeProvider()
    if name not in ("gemini", "openai"):
        raise LLMError(
            f"LLM_PROVIDER must be gemini, openai or fake (got {name!r}). "
            "Copy pipeline/.env.example to pipeline/.env and fill it in."
        )
    model = config.env("LLM_MODEL")
    if not model:
        raise LLMError("Set LLM_MODEL in pipeline/.env to the exact model id from your provider.")
    if name == "gemini":
        key = config.env("GEMINI_API_KEY")
        if not key:
            raise LLMError("Set GEMINI_API_KEY in pipeline/.env.")
        return GeminiProvider(model, key)
    base_url = config.env("OPENAI_BASE_URL")
    key = config.env("OPENAI_API_KEY")
    if not key and not base_url:
        raise LLMError("Set OPENAI_API_KEY (or OPENAI_BASE_URL for a local server) in pipeline/.env.")
    return OpenAICompatProvider(model, key, base_url)


# --------------------------------------------------------------------------- core call


def cache_key(
    provider: str, model: str, system: str | None, prompt: str, schema: dict[str, Any]
) -> str:
    payload = json.dumps(
        {
            "v": CACHE_VERSION,
            "provider": provider,
            "model": model,
            "system": system,
            "prompt": prompt,
            "schema": schema,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse(raw: str, model_cls: type[BaseModel] | None) -> Any:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise LLMError(f"Reply is not valid JSON: {raw[:200]!r}") from exc
    if model_cls is None:
        return data
    try:
        return model_cls.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        raise LLMError(f"Reply does not match {model_cls.__name__}: {exc}") from exc


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def complete_json(
    prompt: str,
    schema: type[BaseModel] | dict[str, Any],
    *,
    system: str | None = None,
    provider: Provider | None = None,
    use_cache: bool = True,
) -> Any:
    """Ask the LLM for JSON matching `schema`; return the validated, JSON-ready result.

    Invalid replies raise LLMError and are never cached.
    """
    provider = provider or get_provider()
    model_cls = schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None
    json_schema = model_cls.model_json_schema() if model_cls else schema

    key = cache_key(provider.name, provider.model, system, prompt, json_schema)
    path = config.llm_cache_dir() / f"{key}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["output"]

    try:
        raw = provider.complete_json(prompt, json_schema, system)
    except LLMError:  # includes QuotaError
        raise
    except Exception as exc:  # SDK errors: auth, quota, network
        raise LLMError(f"{provider.name} call failed: {type(exc).__name__}: {exc}") from exc

    output = _parse(raw, model_cls)
    if use_cache:
        _atomic_write_json(
            path,
            {
                "provider": provider.name,
                "model": getattr(provider, "last_model", provider.model),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "output": output,
            },
        )
    return output


# --------------------------------------------------------------------------- CLI


class _CheckReply(BaseModel):
    city: str


def _check() -> int:
    try:
        provider = get_provider()
        out = complete_json(
            "What is the capital of France? Reply as JSON with a single field 'city'.",
            _CheckReply,
            provider=provider,
            use_cache=False,
        )
    except LLMError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK  provider={provider.name}  model={getattr(provider, 'last_model', provider.model)}  reply={out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.llm")
    parser.add_argument(
        "--check", action="store_true", help="make one tiny live call to verify key and model"
    )
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
