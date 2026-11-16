import json

import pytest
from pydantic import BaseModel

from pipeline import llm
from pipeline.llm import FakeProvider, LLMError, complete_json


class Reply(BaseModel):
    city: str
    population_m: float | None = None


def test_second_identical_call_is_served_from_cache():
    fake = FakeProvider(lambda p, s: {"city": "Paris"})
    first = complete_json("capital of France?", Reply, provider=fake)
    second = complete_json("capital of France?", Reply, provider=fake)
    assert first == second == {"city": "Paris", "population_m": None}
    assert fake.calls == 1


def test_different_prompt_or_system_misses_cache():
    fake = FakeProvider(lambda p, s: {"city": "X"})
    complete_json("a", Reply, provider=fake)
    complete_json("b", Reply, provider=fake)
    complete_json("a", Reply, provider=fake, system="be terse")
    assert fake.calls == 3


def test_different_model_misses_cache():
    complete_json("a", Reply, provider=FakeProvider(lambda p, s: {"city": "X"}, model="m1"))
    other = FakeProvider(lambda p, s: {"city": "Y"}, model="m2")
    assert complete_json("a", Reply, provider=other)["city"] == "Y"
    assert other.calls == 1


def test_invalid_reply_raises_and_is_not_cached(tmp_path):
    bad = FakeProvider(lambda p, s: {"town": "Paris"})
    with pytest.raises(LLMError):
        complete_json("q", Reply, provider=bad)
    assert not any(tmp_path.joinpath("llm_cache").glob("*.json"))

    good = FakeProvider(lambda p, s: {"city": "Paris"})
    assert complete_json("q", Reply, provider=good)["city"] == "Paris"
    assert good.calls == 1


def test_non_json_reply_raises():
    with pytest.raises(LLMError, match="not valid JSON"):
        complete_json("q", Reply, provider=FakeProvider(lambda p, s: "Paris, obviously"))


def test_fenced_json_is_accepted():
    fenced = '```json\n{"city": "Paris"}\n```'
    assert complete_json("q", Reply, provider=FakeProvider(lambda p, s: fenced))["city"] == "Paris"


def test_plain_json_schema_dict_is_supported():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    fake = FakeProvider(lambda p, s: {"n": 3})
    assert complete_json("q", schema, provider=fake) == {"n": 3}


def test_provider_receives_the_json_schema():
    seen = {}

    def responder(prompt, schema):
        seen.update(schema)
        return {"city": "Paris"}

    complete_json("q", Reply, provider=FakeProvider(responder))
    assert seen["properties"]["city"]["type"] == "string"


def test_use_cache_false_always_calls_provider():
    fake = FakeProvider(lambda p, s: {"city": "Paris"})
    complete_json("q", Reply, provider=fake, use_cache=False)
    complete_json("q", Reply, provider=fake, use_cache=False)
    assert fake.calls == 2


def test_provider_exceptions_are_wrapped():
    def boom(prompt, schema):
        raise ConnectionError("network down")

    with pytest.raises(LLMError, match="ConnectionError"):
        complete_json("q", Reply, provider=FakeProvider(boom))


def test_cache_file_records_provider_and_model(tmp_path):
    complete_json("q", Reply, provider=FakeProvider(lambda p, s: {"city": "Paris"}, model="m9"))
    (entry,) = tmp_path.joinpath("llm_cache").glob("*.json")
    data = json.loads(entry.read_text(encoding="utf-8"))
    assert data["provider"] == "fake" and data["model"] == "m9"
    assert data["output"]["city"] == "Paris"


@pytest.mark.parametrize(
    "env, message",
    [
        ({}, "LLM_PROVIDER must be"),
        ({"LLM_PROVIDER": "gemini"}, "LLM_MODEL"),
        ({"LLM_PROVIDER": "openai", "LLM_MODEL": "m"}, "OPENAI_API_KEY"),
    ],
)
def test_get_provider_explains_missing_settings(monkeypatch, env, message):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(LLMError, match=message):
        llm.get_provider()


def test_fake_provider_selectable_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert llm.get_provider().name == "fake"


class _Quota(Exception):
    code = 429


def _gemini(monkeypatch, models, behaviour):
    """A GeminiProvider whose client is replaced by `behaviour(model) -> text or exception`."""
    pytest.importorskip("google.genai")
    provider = llm.GeminiProvider(models, "test-key")
    asked = []

    class _Models:
        def generate_content(self, model, contents, config):
            asked.append(model)
            out = behaviour(model)
            if isinstance(out, Exception):
                raise out
            return type("Resp", (), {"text": out})()

    provider._client = type("Client", (), {"models": _Models()})()
    return provider, asked


def test_gemini_falls_back_to_the_next_model_when_quota_runs_out(monkeypatch):
    provider, asked = _gemini(
        monkeypatch, "flash, flash-lite ,old-lite",
        lambda model: _Quota("RESOURCE_EXHAUSTED") if model == "flash" else '{"city": "Paris"}',
    )
    assert provider.model == "flash"  # the cache key stays on the first model
    assert complete_json("capital?", Reply, provider=provider, use_cache=False)["city"] == "Paris"
    assert asked == ["flash", "flash-lite"] and provider.last_model == "flash-lite"
    complete_json("again?", Reply, provider=provider, use_cache=False)
    assert asked == ["flash", "flash-lite", "flash-lite"]  # the exhausted model is not asked again


def test_gemini_raises_quota_error_when_every_model_is_exhausted(monkeypatch):
    provider, asked = _gemini(monkeypatch, "flash,flash-lite", lambda model: _Quota("quota"))
    with pytest.raises(llm.QuotaError, match="flash, flash-lite"):
        complete_json("capital?", Reply, provider=provider, use_cache=False)
    assert asked == ["flash", "flash-lite"]


class _Stalled(Exception):
    code = 504


def test_gemini_skips_a_stalled_model_and_remembers_it(monkeypatch):
    provider, asked = _gemini(
        monkeypatch, "flash,flash-lite",
        lambda model: _Stalled("504 DEADLINE_EXCEEDED") if model == "flash" else '{"city": "Paris"}',
    )
    assert complete_json("capital?", Reply, provider=provider, use_cache=False)["city"] == "Paris"
    complete_json("again?", Reply, provider=provider, use_cache=False)
    assert asked == ["flash", "flash-lite", "flash-lite"]  # one timeout, not one per question


def test_gemini_asks_a_stalled_model_again_after_the_pause(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    stalled = {"flash"}
    provider, asked = _gemini(
        monkeypatch, "flash,flash-lite",
        lambda model: _Stalled("timed out") if model in stalled else '{"city": "Paris"}',
    )
    complete_json("capital?", Reply, provider=provider, use_cache=False)
    stalled.clear()
    clock[0] += llm.SLOW_MODEL_SKIP_S + 1
    complete_json("again?", Reply, provider=provider, use_cache=False)
    assert asked == ["flash", "flash-lite", "flash"] and provider.last_model == "flash"


def test_gemini_reports_overload_when_every_model_is_slow(monkeypatch):
    provider, asked = _gemini(monkeypatch, "flash,flash-lite", lambda model: _Stalled("503 UNAVAILABLE"))
    with pytest.raises(llm.OverloadedError, match="flash, flash-lite") as caught:
        provider.complete_json("capital?", {}, None)
    assert llm._overloaded(caught.value) and not isinstance(caught.value, llm.QuotaError)


def test_chain_hands_a_slow_gemini_to_the_fallback(monkeypatch):
    gemini, asked = _gemini(monkeypatch, "flash,flash-lite", lambda model: _Stalled("timed out"))
    nim = _Step("nim", '{"city": "Paris"}')
    chain = llm.ChainProvider([gemini, nim])
    assert complete_json("capital?", Reply, provider=chain, use_cache=False)["city"] == "Paris"
    complete_json("again?", Reply, provider=chain, use_cache=False)
    assert asked == ["flash", "flash-lite"] and nim.calls == 2  # the slow models wait out their pause


def test_gemini_other_errors_are_not_treated_as_quota(monkeypatch):
    provider, asked = _gemini(monkeypatch, "flash,flash-lite", lambda model: ValueError("bad key"))
    with pytest.raises(LLMError, match="bad key") as caught:
        complete_json("capital?", Reply, provider=provider, use_cache=False)
    assert not isinstance(caught.value, llm.QuotaError) and asked == ["flash"]


SETTINGS = ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "OPENAI_MODEL", "OPENAI_EXTRA_BODY", "LLM_FALLBACK", "LLM_TIMEOUT_S")


def _env(monkeypatch, **values):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


class _Step:
    """A provider stand-in that replies, raises QuotaError, or raises another error."""

    def __init__(self, name, behaviour):
        self.name, self.model, self.behaviour, self.calls = name, f"{name}-model", behaviour, 0

    def complete_json(self, prompt, schema, system):
        self.calls += 1
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


def test_chain_moves_on_when_a_quota_runs_out():
    first, second = _Step("gemini", llm.QuotaError("used up")), _Step("openai", '{"city": "Paris"}')
    chain = llm.ChainProvider([first, second])
    assert complete_json("capital?", Reply, provider=chain, use_cache=False)["city"] == "Paris"
    assert chain.last_model == "openai:openai-model" and chain.name == "gemini+openai"
    complete_json("again?", Reply, provider=chain, use_cache=False)
    assert (first.calls, second.calls) == (1, 2)  # the exhausted provider is not asked again


def test_chain_moves_on_after_a_timeout():
    first, second = _Step("gemini", TimeoutError("slow")), _Step("openai", '{"city": "Paris"}')
    out = complete_json("capital?", Reply, provider=llm.ChainProvider([first, second]), use_cache=False)
    assert out["city"] == "Paris" and second.calls == 1


def test_chain_does_not_hide_other_errors():
    first, second = _Step("gemini", ValueError("bad request")), _Step("openai", '{"city": "Paris"}')
    with pytest.raises(LLMError, match="bad request"):
        complete_json("capital?", Reply, provider=llm.ChainProvider([first, second]), use_cache=False)
    assert second.calls == 0


def test_chain_reports_quota_when_every_provider_is_used_up():
    chain = llm.ChainProvider([_Step("gemini", llm.QuotaError("a")), _Step("openai", llm.QuotaError("b"))])
    with pytest.raises(llm.QuotaError, match="every provider"):
        complete_json("capital?", Reply, provider=chain, use_cache=False)


class _Completions:
    def __init__(self, behaviour):
        self.behaviour, self.kwargs = behaviour, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        message = type("Message", (), {"content": self.behaviour})()
        return type("Resp", (), {"choices": [type("Choice", (), {"message": message})()]})()


def _openai(monkeypatch, behaviour, **env):
    pytest.importorskip("openai")
    _env(monkeypatch, **env)
    provider = llm.OpenAICompatProvider("nvidia/model", "key", "https://example.test/v1",
                                        extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    completions = _Completions(behaviour)
    provider._client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return provider, completions


def test_openai_provider_strips_thinking_and_sends_options(monkeypatch):
    provider, completions = _openai(monkeypatch, '<think>let me see</think>\n{"city": "Paris"}')
    assert complete_json("capital?", Reply, provider=provider, use_cache=False)["city"] == "Paris"
    assert completions.kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert completions.kwargs["response_format"]["type"] == "json_schema"


def test_openai_rate_limit_is_a_quota_error(monkeypatch):
    error = Exception("Too Many Requests")
    error.status_code = 429
    provider, _ = _openai(monkeypatch, error)
    with pytest.raises(llm.QuotaError):
        complete_json("capital?", Reply, provider=provider, use_cache=False)


def test_openai_client_has_no_retries_and_the_timeout(monkeypatch):
    pytest.importorskip("openai")
    _env(monkeypatch, LLM_TIMEOUT_S="10")
    provider = llm.OpenAICompatProvider("m", "key", "https://example.test/v1")
    assert provider._client.max_retries == 0 and provider._client.timeout == 10.0


def test_get_provider_builds_gemini_then_nvidia(monkeypatch):
    pytest.importorskip("openai")
    pytest.importorskip("google.genai")
    _env(monkeypatch, LLM_PROVIDER="gemini", LLM_MODEL="gemini-x", GEMINI_API_KEY="g", LLM_FALLBACK="openai",
         OPENAI_MODEL="nvidia/nemotron", OPENAI_API_KEY="n", OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1",
         OPENAI_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}')
    chain = llm.get_provider()
    assert isinstance(chain, llm.ChainProvider)
    assert [p.name for p in chain.providers] == ["gemini", "openai"]
    assert chain.providers[1].model == "nvidia/nemotron"
    assert chain.providers[1]._extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize("env, message", [
    ({"OPENAI_EXTRA_BODY": "{not json"}, "OPENAI_EXTRA_BODY is not valid JSON"),
    ({"OPENAI_MODEL": ""}, "needs OPENAI_MODEL"),
])
def test_fallback_settings_are_checked(monkeypatch, env, message):
    pytest.importorskip("openai")
    pytest.importorskip("google.genai")
    base = {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-x", "GEMINI_API_KEY": "g", "LLM_FALLBACK": "openai",
            "OPENAI_MODEL": "m", "OPENAI_API_KEY": "n"}
    _env(monkeypatch, **{k: v for k, v in {**base, **env}.items() if v})
    with pytest.raises(LLMError, match=message):
        llm.get_provider()


def test_without_fallback_gemini_is_used_alone(monkeypatch):
    pytest.importorskip("google.genai")
    _env(monkeypatch, LLM_PROVIDER="gemini", LLM_MODEL="gemini-x", GEMINI_API_KEY="g")
    assert isinstance(llm.get_provider(), llm.GeminiProvider)


def test_chain_moves_on_when_a_provider_is_overloaded():
    busy = Exception("503 UNAVAILABLE. The model is overloaded.")
    busy.code = 503
    first, second = _Step("gemini", busy), _Step("openai", '{"city": "Paris"}')
    chain = llm.ChainProvider([first, second])
    assert complete_json("capital?", Reply, provider=chain, use_cache=False)["city"] == "Paris"
    complete_json("again?", Reply, provider=chain, use_cache=False)
    assert first.calls == 2  # overloaded is not used up: it is tried again next time


def test_overloaded_last_provider_is_an_error():
    busy = Exception("503 UNAVAILABLE")
    chain = llm.ChainProvider([_Step("gemini", llm.QuotaError("used")), _Step("openai", busy)])
    with pytest.raises(LLMError, match="UNAVAILABLE"):
        complete_json("capital?", Reply, provider=chain, use_cache=False)
