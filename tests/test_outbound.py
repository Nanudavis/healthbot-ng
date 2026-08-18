"""Async outbound WhatsApp queue: idempotent enqueue, worker processing,
retries with backoff, and the webhook async path."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app import config, conversation, db, outbound, security
from app.main import app
from app.models import OutboundMessage

client = TestClient(app)


def test_enqueue_is_idempotent_per_message_sid():
    assert outbound.enqueue("whatsapp:+2348011111111", "hello", message_sid="SM1") is True
    assert outbound.enqueue("whatsapp:+2348011111111", "hello", message_sid="SM1") is False
    rows = outbound.outbound_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_worker_processes_and_marks_sent(monkeypatch):
    sent = {}

    def fake_handle(phone, body, latitude=None, longitude=None):
        sent["phone"] = phone
        sent["body"] = body
        return "Rest and drink water."

    monkeypatch.setattr(conversation, "handle_message", fake_handle)

    def fake_send(to_number, body):
        sent["to_number"] = to_number
        sent["out_body"] = body
        return "SM123"

    monkeypatch.setattr(outbound, "send_via_twilio", fake_send)
    outbound.enqueue("whatsapp:+2348011111111", "my head dey pain", message_sid="SM2")
    assert outbound.process_due() == 1
    rows = outbound.outbound_rows()
    assert rows[0]["status"] == "sent"
    assert rows[0]["provider_message_id"] == "SM123"
    assert sent["phone"] == "whatsapp:+2348011111111"
    assert "Rest and drink water." in sent["out_body"]
    assert "does not replace a doctor" in sent["out_body"]


def test_failures_retry_with_backoff_then_fail(monkeypatch):
    monkeypatch.setattr(config, "OUTBOUND_MAX_ATTEMPTS", 2)

    def fake_send(to_number, body):
        raise RuntimeError("boom")

    monkeypatch.setattr(outbound, "send_via_twilio", fake_send)
    monkeypatch.setattr(conversation, "handle_message", lambda *a, **k: "reply")
    outbound.enqueue("whatsapp:+2348011111111", "x", message_sid="SM3")

    assert outbound.process_due() == 1
    row = outbound.outbound_rows()[0]
    assert row["status"] == "retrying"
    assert row["attempts"] == 1
    assert "boom" in row["last_error"]

    with db.get_session() as s:
        s.execute(
            update(OutboundMessage).values(
                next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None)
            )
        )
        s.commit()
    assert outbound.process_due() == 1
    row = outbound.outbound_rows()[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 2


def test_async_webhook_enqueues_and_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_ASYNC_OUTBOUND", True)
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "ACx")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(config, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

    async def _noop(request):
        return None

    monkeypatch.setattr(security, "verify_twilio_signature", _noop)
    r = client.post(
        "/webhook/whatsapp",
        data={"Body": "hello", "From": "whatsapp:+2348011111111", "MessageSid": "SM-ASYNC"},
    )
    assert r.status_code == 200
    assert "<Message>" not in r.text  # acknowledged immediately
    rows = outbound.outbound_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["message_sid"] == "SM-ASYNC"


def test_async_falls_back_to_sync_without_credentials(monkeypatch, fake_llm):
    monkeypatch.setattr(config, "WHATSAPP_ASYNC_OUTBOUND", True)
    monkeypatch.setattr(config, "TWILIO_WHATSAPP_NUMBER", "")
    r = client.post(
        "/webhook/whatsapp",
        data={"Body": "hello", "From": "whatsapp:+2348011111111"},
    )
    assert r.status_code == 200
    assert "<Message>" in r.text  # synchronous TwiML reply preserved


def test_outbound_endpoint():
    outbound.enqueue("whatsapp:+2348011111111", "x", message_sid="SM-EP")
    r = client.get("/api/observability/outbound")
    assert r.status_code == 200
    assert r.json()[0]["message_sid"] == "SM-EP"
