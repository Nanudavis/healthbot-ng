"""Resilience: timeouts, transient failures, and honest reporting.

The concern behind these tests is that an infrastructure problem could
masquerade as a clinical result. A rate-limited evaluation run must not
report a less accurate model, and a slow provider must not leave a
patient with no reply at all.
"""

import pytest

from app import config, conversation, security
from scripts import evaluate


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Keep the retry logic under test but skip the actual waiting."""
    monkeypatch.setattr(conversation.time, "sleep", lambda s: None)
    conversation._unsupported.clear()
    yield
    conversation._unsupported.clear()


class Flaky:
    """Fails a set number of times, then succeeds."""

    def __init__(self, failures, exc):
        self.remaining = failures
        self.exc = exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc

        class Msg:
            content = '{"triage":"CLINIC","reason":"r","reply":"See a health worker."}'

        class Choice:
            message = Msg()

        class Response:
            choices = [Choice()]

        return Response()


def install(monkeypatch, completions):
    class Client:
        chat = type("Chat", (), {"completions": completions})()

    monkeypatch.setattr(conversation, "_client", lambda: Client())
    return completions


# ── Classifying failures ────────────────────────────────────────

@pytest.mark.parametrize(
    "message",
    [
        "Rate limit reached for gpt-4o",
        "429 Too Many Requests",
        "503 Service Unavailable",
        "502 Bad Gateway",
        "Request timed out",
        "Connection error",
        "The server is overloaded",
    ],
)
def test_transient_failures_are_recognised(message):
    assert conversation.is_transient(Exception(message))


@pytest.mark.parametrize(
    "message",
    [
        "Incorrect API key provided",
        "model `gpt-9` does not exist",
        "unauthorized client detected",
        "context length exceeded",
    ],
)
def test_permanent_failures_are_not_retried(message):
    """Retrying a bad key just wastes time and hides the real problem."""
    assert not conversation.is_transient(Exception(message))


def test_status_code_attribute_is_honoured():
    exc = Exception("something")
    exc.status_code = 429
    assert conversation.is_transient(exc)


# ── Retry behaviour ─────────────────────────────────────────────

def test_rate_limit_is_retried_and_succeeds(monkeypatch):
    flaky = install(monkeypatch, Flaky(2, Exception("429 rate limit reached")))
    out = conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "CLINIC" in out
    assert flaky.calls == 3  # two failures then success


def test_retries_are_bounded(monkeypatch):
    flaky = install(monkeypatch, Flaky(99, Exception("429 rate limit")))
    with pytest.raises(Exception, match="rate limit"):
        conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert flaky.calls <= config.LLM_MAX_RETRIES + 1


def test_permanent_error_fails_immediately(monkeypatch):
    flaky = install(monkeypatch, Flaky(99, Exception("Incorrect API key provided")))
    with pytest.raises(Exception, match="API key"):
        conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert flaky.calls == 1  # no pointless retries


def test_backoff_grows_and_is_capped():
    delays = [conversation._retry_delay(i, Exception("429")) for i in range(6)]
    assert delays[0] < delays[3]
    assert all(d <= config.LLM_MAX_BACKOFF_SECONDS for d in delays)


def test_retry_after_header_is_honoured():
    class Resp:
        headers = {"retry-after": "7"}

    exc = Exception("429")
    exc.response = Resp()
    assert conversation._retry_delay(0, exc) == 7


def test_absurd_retry_after_is_capped():
    class Resp:
        headers = {"retry-after": "99999"}

    exc = Exception("429")
    exc.response = Resp()
    assert conversation._retry_delay(0, exc) <= config.LLM_MAX_BACKOFF_SECONDS


def test_timeout_is_below_the_twilio_deadline():
    """Twilio abandons a webhook at 15s; the model call must give up
    first so the safe fallback still goes out."""
    assert config.LLM_TIMEOUT_SECONDS < 15


def test_patient_still_gets_a_reply_when_the_provider_is_down(monkeypatch):
    install(monkeypatch, Flaky(99, Exception("Connection error")))
    reply = conversation.handle_message("whatsapp:+2348011111111", "I get headache")
    assert reply == conversation.FALLBACKS["english"]
    assert "clinic" in reply.lower()


# ── Honest evaluation reporting ─────────────────────────────────

def _rows():
    return [
        {"id": "v1", "language": "english", "expected": "EMERGENCY", "predicted": "EMERGENCY"},
        {"id": "v2", "language": "pidgin", "expected": "CLINIC", "predicted": "CLINIC"},
        {"id": "v3", "language": "hausa", "expected": "CLINIC", "predicted": "ERROR"},
        {"id": "v4", "language": "yoruba", "expected": "SELF_CARE", "predicted": "CLINIC"},
    ]


def test_api_failures_counted_separately_from_clinical_errors():
    m = evaluate.score(_rows())
    assert m["errors"] == 1
    assert m["error_ids"] == ["v3"]
    assert m["scored"] == 3
    # Raw accuracy counts the failure as wrong; the adjusted figure does not.
    assert m["accuracy"] == 0.5
    assert round(m["accuracy_excluding_errors"], 2) == 0.67


def test_report_warns_when_vignettes_never_reached_the_model():
    report = evaluate.render_report(evaluate.score(_rows()))
    assert "never reached the model" in report
    assert "not clinical" in report
    assert "Re-run before reporting" in report


def test_clean_run_has_no_warning():
    rows = [r for r in _rows() if r["predicted"] != "ERROR"]
    report = evaluate.render_report(evaluate.score(rows))
    assert "never reached the model" not in report


def test_no_errors_means_the_two_accuracies_agree():
    rows = [r for r in _rows() if r["predicted"] != "ERROR"]
    m = evaluate.score(rows)
    assert m["accuracy"] == m["accuracy_excluding_errors"]


# ── Rate-limiter housekeeping ───────────────────────────────────

def test_idle_senders_are_forgotten():
    """Every number that ever messages would otherwise leak an entry."""
    security.reset_rate_limits()
    for i in range(200):
        security.check_rate_limit(f"+234800{i:04d}")
    assert security.tracked_senders() == 200

    for window in security._seen.values():
        window.clear()
    security._last_sweep = 0
    security.check_rate_limit("+2349999999999")
    assert security.tracked_senders() == 1
    security.reset_rate_limits()


def test_active_senders_survive_the_sweep():
    security.reset_rate_limits()
    security.check_rate_limit("+2348011111111")
    security._last_sweep = 0
    security.check_rate_limit("+2348022222222")
    assert security.tracked_senders() == 2
    security.reset_rate_limits()
