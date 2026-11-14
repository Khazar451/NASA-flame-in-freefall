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
    data = json.loads(entry.read_text())
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
