import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from pipeline import api, ask, llm  # noqa: E402
from pipeline.tests.test_ask import INVESTIGATIONS, PASSAGE, RECORD, TESTS, FakeSearch  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ASK_RATE_PER_MINUTE", "3")
    monkeypatch.setenv("LLM_DAILY_LIMIT", "5")
    kb = ask.KnowledgeBase(INVESTIGATIONS, TESTS, search=FakeSearch([PASSAGE, PASSAGE, RECORD]))
    provider = llm.FakeProvider(lambda p, s: {"answer": "FLEX tested carbon dioxide at 30 % oxygen.", "sources_used": [1]})
    with TestClient(api.create_app(kb=kb, provider=provider)) as test_client:
        yield test_client


def test_reply_has_the_shape_the_dashboard_expects(client):
    res = client.post("/ask", json={"question": "What did FLEX find about carbon dioxide?"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"answer", "citations", "mode"} and body["mode"] == "ai"
    assert all({"label", "url"} <= set(c) for c in body["citations"])
    urls = [c["url"] for c in body["citations"]]
    assert len(urls) == len(set(urls))  # React keys the list on url


def test_cors_allows_the_dashboard(client):
    res = client.options("/ask", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert res.status_code == 200 and res.headers["access-control-allow-origin"] in ("*", "http://localhost:5173")


def test_bad_questions_are_rejected(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/ask", json={}).status_code == 422


def test_rate_limit(client):
    codes = [client.post("/ask", json={"question": "What oxygen levels did FLEX test?"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_health_reports_what_is_loaded(client):
    body = client.get("/health").json()
    assert body == {"status": "ok", "investigations": len(INVESTIGATIONS), "tests": len(TESTS), "search": True,
                    "demo_answers": 0, "ai": True, "ai_calls_left_today": 5}


def test_without_a_provider_answers_are_retrieval_only(monkeypatch):
    kb = ask.KnowledgeBase(INVESTIGATIONS, TESTS, search=FakeSearch([PASSAGE]))
    with TestClient(api.create_app(kb=kb, use_ai=False)) as test_client:
        body = test_client.post("/ask", json={"question": "What did FLEX find about carbon dioxide?"}).json()
        assert body["mode"] == "retrieval"
        assert test_client.get("/health").json()["ai"] is False


def test_bare_address_says_the_service_is_running(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "running" and res.json()["health"] == "/health"
    assert client.head("/").status_code in (200, 405)  # Render's own checks send HEAD /
