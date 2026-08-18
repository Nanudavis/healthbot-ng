"""Webhook authenticity, abuse limits and session expiry."""

import time

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app import config, conversation, security
from app.main import app
from app.sessions import SessionStore

client = TestClient(app)
TOKEN = "test_auth_token_0123456789abcdef"


@pytest.fixture(autouse=True)
def clean_limits():
    security.reset_rate_limits()
    yield
    security.reset_rate_limits()


def post_whatsapp(**fields):
    data = {"Body": "hello", "From": "whatsapp:+2348011111111", **fields}
    return client.post("/webhook/whatsapp", data=data)


# ── Twilio signature verification ───────────────────────────────

def test_unsigned_requests_allowed_when_token_unset(monkeypatch, fake_llm):
    """Local development and the test suite must still work."""
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    assert post_whatsapp().status_code == 200


def test_unsigned_request_rejected_when_token_set(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    r = post_whatsapp()
    assert r.status_code == 403
    assert "signature" in r.json()["detail"].lower()


def test_bad_signature_rejected(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    r = client.post(
        "/webhook/whatsapp",
        data={"Body": "hello", "From": "whatsapp:+2348011111111"},
        headers={"X-Twilio-Signature": "obviously-not-valid"},
    )
    assert r.status_code == 403


def test_correctly_signed_request_accepted(monkeypatch, fake_llm):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://testserver")

    fields = {"Body": "I get small headache", "From": "whatsapp:+2348011111111"}
    url = "http://testserver/webhook/whatsapp"
    signature = RequestValidator(TOKEN).compute_signature(url, fields)

    r = client.post(
        "/webhook/whatsapp", data=fields, headers={"X-Twilio-Signature": signature}
    )
    assert r.status_code == 200
    assert "<Response>" in r.text


def test_forged_symptom_report_is_blocked(monkeypatch):
    """The point of signature checking: someone who finds the URL must
    not be able to inject fake surveillance data."""
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    r = client.post(
        "/webhook/whatsapp",
        data={"Body": "convulsion", "From": "whatsapp:+2340000000000"},
    )
    assert r.status_code == 403


# ── Rate limiting ───────────────────────────────────────────────

def test_rate_limit_allows_normal_use():
    for _ in range(security.RATE_LIMIT_MESSAGES):
        security.check_rate_limit("+2348011111111")  # no raise


def test_rate_limit_blocks_flood():
    from fastapi import HTTPException

    for _ in range(security.RATE_LIMIT_MESSAGES):
        security.check_rate_limit("+2348011111111")
    with pytest.raises(HTTPException) as exc:
        security.check_rate_limit("+2348011111111")
    assert exc.value.status_code == 429


def test_rate_limit_is_per_sender():
    from fastapi import HTTPException

    for _ in range(security.RATE_LIMIT_MESSAGES):
        security.check_rate_limit("+2348011111111")
    # A different patient must not be punished for someone else's flood.
    security.check_rate_limit("+2348022222222")
    with pytest.raises(HTTPException):
        security.check_rate_limit("+2348011111111")


def test_rate_limit_window_slides(monkeypatch):
    from fastapi import HTTPException

    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    for _ in range(security.RATE_LIMIT_MESSAGES):
        security.check_rate_limit("+234801")
    with pytest.raises(HTTPException):
        security.check_rate_limit("+234801")
    clock[0] += security.RATE_LIMIT_WINDOW_SECONDS + 1
    security.check_rate_limit("+234801")  # window has passed


def test_webhook_returns_429_on_flood(monkeypatch, fake_llm):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    codes = {post_whatsapp().status_code for _ in range(security.RATE_LIMIT_MESSAGES + 3)}
    assert 429 in codes


def test_ussd_is_rate_limited(monkeypatch):
    from fastapi import HTTPException

    for _ in range(security.RATE_LIMIT_MESSAGES):
        security.check_rate_limit("+2348033333333")
    with pytest.raises(HTTPException):
        security.check_rate_limit("+2348033333333")


# ── Session expiry ──────────────────────────────────────────────

def test_stale_session_is_dropped():
    """A complaint from last week must not become context for today's."""
    store = SessionStore(ttl_seconds=1)
    store.append("sid", "user", "my pikin dey hot")
    assert len(store.history("sid")) == 1
    time.sleep(1.1)
    assert store.history("sid") == []


def test_active_session_survives():
    store = SessionStore(ttl_seconds=60)
    store.append("sid", "user", "first")
    store.append("sid", "assistant", "question?")
    assert len(store.history("sid")) == 2


def test_activity_refreshes_the_clock():
    store = SessionStore(ttl_seconds=2)
    store.append("sid", "user", "first")
    time.sleep(1.2)
    store.append("sid", "user", "second")  # refreshes
    time.sleep(1.2)
    assert len(store.history("sid")) == 2  # would be gone without refresh


def test_expiry_clears_language_metadata():
    store = SessionStore(ttl_seconds=1)
    store.set_meta("sid", "language", "hausa")
    time.sleep(1.1)
    assert store.get_meta("sid", "language") is None


def test_purge_expired_reports_count():
    store = SessionStore(ttl_seconds=1)
    store.append("a", "user", "x")
    store.append("b", "user", "y")
    time.sleep(1.1)
    store.append("c", "user", "z")
    assert store.purge_expired() == 2
    assert store.active_count() == 1


# ── Status reporting ────────────────────────────────────────────

def test_status_warns_when_signature_checking_is_off(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    status = security.security_status()
    assert status["twilio_signature_verification"] is False
    assert status["warnings"], "an unprotected deployment must be flagged"


def test_status_clean_when_configured(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    status = security.security_status()
    assert status["twilio_signature_verification"] is True
    assert status["warnings"] == []


def test_status_endpoint(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    body = client.get("/api/security/status").json()
    assert body["twilio_signature_verification"] is False
    assert "active_sessions" in body
    assert body["rate_limit_per_minute"] == security.RATE_LIMIT_MESSAGES


def test_status_never_leaks_the_token(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", TOKEN)
    assert TOKEN not in client.get("/api/security/status").text
