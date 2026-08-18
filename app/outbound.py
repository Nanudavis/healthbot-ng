"""Async outbound WhatsApp replies.

When WHATSAPP_ASYNC_OUTBOUND is enabled, the webhook acknowledges
immediately and a background worker runs the triage pipeline and sends
the reply through the Twilio REST API. The queue is database-backed and
idempotent per MessageSid; failures retry with exponential backoff.
The synchronous TwiML path remains the default and is unchanged.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import config, conversation, db
from app.models import OutboundMessage

log = logging.getLogger(__name__)

DISCLAIMER = "HealthBot NG gives guidance only — it does not replace a doctor."


def outbound_available() -> bool:
    return bool(
        config.TWILIO_ACCOUNT_SID
        and config.TWILIO_AUTH_TOKEN
        and config.TWILIO_WHATSAPP_NUMBER
    )


def _client():
    from twilio.rest import Client

    return Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def send_via_twilio(to_number: str, body: str) -> str:
    """Send a WhatsApp message via the Twilio REST API; returns the
    provider message SID and raises on failure."""
    message = _client().messages.create(
        from_=config.TWILIO_WHATSAPP_NUMBER, to=to_number, body=body
    )
    return message.sid


def enqueue(
    to_number: str,
    body: str,
    latitude: float | None = None,
    longitude: float | None = None,
    message_sid: str | None = None,
) -> bool:
    """Queue a reply, idempotent per MessageSid. Returns True when a new
    row was created (False for a duplicate retry)."""
    db.init_db()
    with db.get_session() as session:
        if message_sid:
            existing = session.scalar(
                select(OutboundMessage).where(
                    OutboundMessage.message_sid == message_sid
                )
            )
            if existing is not None:
                return False
        session.add(
            OutboundMessage(
                message_sid=message_sid,
                to_number=to_number,
                body=body,
                latitude=latitude,
                longitude=longitude,
                status="pending",
                attempts=0,
                next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        session.commit()
    return True


def _format_reply(reply: str) -> str:
    return f"{reply}\n\n_{DISCLAIMER}_"


def _process_row(row, now_naive) -> None:
    """Run the triage pipeline for one queued message and send the reply."""
    try:
        reply = conversation.handle_message(
            row.to_number,
            row.body,
            row.latitude,
            row.longitude,
        )
        provider_sid = send_via_twilio(row.to_number, _format_reply(reply))
        with db.get_session() as session:
            fresh = session.get(OutboundMessage, row.id)
            fresh.status = "sent"
            fresh.provider_message_id = provider_sid
            fresh.last_error = None
            fresh.updated_at = now_naive
            session.commit()
        log.info("Outbound reply sent to %s", row.to_number)
    except Exception as exc:
        attempts = row.attempts + 1
        with db.get_session() as session:
            fresh = session.get(OutboundMessage, row.id)
            fresh.attempts = attempts
            fresh.last_error = f"{type(exc).__name__}: {exc}"[:300]
            fresh.updated_at = now_naive
            if attempts >= config.OUTBOUND_MAX_ATTEMPTS:
                fresh.status = "failed"
            else:
                fresh.status = "retrying"
                fresh.next_attempt_at = now_naive + timedelta(
                    seconds=config.OUTBOUND_RETRY_BASE_SECONDS * (2 ** (attempts - 1))
                )
            session.commit()
        log.warning(
            "Outbound attempt %d/%d failed for %s: %s",
            attempts,
            config.OUTBOUND_MAX_ATTEMPTS,
            row.to_number,
            type(exc).__name__,
        )


def process_due() -> int:
    """Process every pending/retrying message whose retry time has come.
    Returns the number processed. Never raises."""
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.get_session() as session:
        rows = session.scalars(
            select(OutboundMessage)
            .where(
                OutboundMessage.status.in_(("pending", "retrying")),
                OutboundMessage.next_attempt_at <= now_naive,
            )
            .order_by(OutboundMessage.id)
            .limit(50)
        ).all()
    count = 0
    for row in rows:
        _process_row(row, now_naive)
        count += 1
    return count


def run_worker(stop_event: threading.Event) -> None:
    """Background loop: drain the queue every poll interval."""
    log.info("Outbound worker started")
    while not stop_event.is_set():
        try:
            process_due()
        except Exception:
            log.exception("Outbound worker cycle failed")
        stop_event.wait(config.OUTBOUND_POLL_SECONDS)
    log.info("Outbound worker stopped")


def outbound_rows(limit: int = 50) -> list[dict]:
    with db.get_session() as session:
        rows = session.scalars(
            select(OutboundMessage)
            .order_by(OutboundMessage.id.desc())
            .limit(limit)
        ).all()
    return [
        {
            "message_sid": r.message_sid,
            "to_number": r.to_number,
            "status": r.status,
            "attempts": r.attempts,
            "last_error": r.last_error,
            "provider_message_id": r.provider_message_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
