"""Observability: request IDs, structured logs, and AI cost/latency events."""

import json
import logging

from fastapi.testclient import TestClient

from app import config, conversation, observability, records
from app.main import app

client = TestClient(app)


class FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class FakeMsg:
    content = '{"triage":"PENDING","reason":"r","reply":"ok"}'


class FakeChoice:
    message = FakeMsg()


class FakeResponse:
    choices = [FakeChoice()]
    usage = FakeUsage()


def test_ai_event_recorded_on_success(monkeypatch):
    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(conversation, "_client", lambda: FakeClient())
    conversation._chat_completion([{"role": "user", "content": "hi"}])
    events = records.ai_events(limit=5)
    assert len(events) == 1
    assert events[0]["ok"] is True
    assert events[0]["prompt_tokens"] == 10
    assert events[0]["completion_tokens"] == 5
    assert events[0]["estimated_cost_usd"] is not None


def test_ai_event_recorded_on_failure(monkeypatch):
    class Failing:
        def create(self, **kwargs):
            raise RuntimeError("boom")

    class FakeClient:
        chat = type("Chat", (), {"completions": Failing()})()

    monkeypatch.setattr(conversation, "_client", lambda: FakeClient())
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)
    try:
        conversation._chat_completion([{"role": "user", "content": "hi"}])
    except RuntimeError:
        pass
    events = records.ai_events(limit=5)
    assert len(events) == 1
    assert events[0]["ok"] is False
    assert events[0]["error_type"] == "RuntimeError"


def test_request_id_header_present():
    r = client.get("/health")
    assert r.status_code == 200
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) == 12


def test_ai_events_endpoint():
    records.log_ai_event(
        provider="deepseek",
        model="deepseek-v4-flash",
        duration_ms=120,
        ok=True,
        prompt_tokens=100,
        completion_tokens=50,
        estimated_cost_usd=0.00003,
    )
    r = client.get("/api/observability/ai-events?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body and body[0]["model"] == "deepseek-v4-flash"
    assert body[0]["duration_ms"] == 120


def test_json_formatter_includes_request_id_and_fields():
    record = logging.LogRecord(
        name="app.conversation",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="chat_call",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc123"
    record.duration_ms = 42
    line = observability.HealthbotJsonFormatter().format(record)
    parsed = json.loads(line)
    assert parsed["request_id"] == "abc123"
    assert parsed["duration_ms"] == 42
    assert parsed["event"] == "chat_call"
