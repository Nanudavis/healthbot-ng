"""Webhook authenticity and abuse limits.

Two problems this solves:

1. Anyone who learns the webhook URL could post fabricated symptom
   reports, corrupting the surveillance data a health authority would
   act on. Twilio signs every request with the account auth token;
   verifying that signature proves the request really came from Twilio.

2. A single sender — malicious or a stuck client — could otherwise loop
   messages and burn the whole API budget, taking the service down for
   real patients.

Signature checking is skipped when TWILIO_AUTH_TOKEN is unset, so local
development and the test suite still work. That default is deliberate
but must not survive into deployment: `security_status()` reports it and
the dashboard surfaces it.
"""

import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app import config

log = logging.getLogger(__name__)

# Generous for a human on WhatsApp; a real person sends a message every
# few seconds at most, so this only catches loops and abuse.
RATE_LIMIT_MESSAGES = 20
RATE_LIMIT_WINDOW_SECONDS = 60

_seen: dict[str, deque] = defaultdict(deque)
# Sweeping on every call would be wasted work; once a minute is enough
# to keep the map proportional to active senders rather than all-time.
SWEEP_INTERVAL_SECONDS = 60
_last_sweep = 0.0

# ── Webhook idempotency ─────────────────────────────────────────
# Twilio retries webhooks that time out or return 5xx. Each retry must
# NOT re-run the triage pipeline (double LLM spend, double records, a
# second reply to the patient). The first successful response is stored
# keyed by MessageSid and replayed verbatim on retries. In-memory with a
# TTL, like the rate limiter; move to a shared store with the session
# store when scaling beyond one process.
MESSAGE_TTL_SECONDS = 600
_seen_messages: dict[str, tuple[float, str]] = {}


def remember_message(message_sid: str, reply_xml: str) -> None:
    """Store the TwiML response for a MessageSid."""
    _seen_messages[message_sid] = (time.monotonic(), reply_xml)
    _sweep_messages()


def message_reply(message_sid: str) -> str | None:
    """The stored response for a MessageSid, or None if unseen/expired."""
    entry = _seen_messages.get(message_sid)
    if entry is None:
        return None
    seen_at, reply = entry
    if time.monotonic() - seen_at > MESSAGE_TTL_SECONDS:
        _seen_messages.pop(message_sid, None)
        return None
    return reply


def _sweep_messages() -> None:
    cutoff = time.monotonic() - MESSAGE_TTL_SECONDS
    stale = [sid for sid, (seen_at, _) in _seen_messages.items() if seen_at < cutoff]
    for sid in stale:
        _seen_messages.pop(sid, None)


def reset_message_dedupe() -> None:
    _seen_messages.clear()


def signature_checking_enabled() -> bool:
    return bool(config.TWILIO_AUTH_TOKEN)


async def verify_twilio_signature(request: Request) -> None:
    """Reject requests not signed by Twilio. No-op when unconfigured."""
    if not signature_checking_enabled():
        return

    from twilio.request_validator import RequestValidator

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        log.warning("Rejected unsigned request to %s", request.url.path)
        raise HTTPException(status_code=403, detail="Missing Twilio signature")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path if config.PUBLIC_BASE_URL else str(request.url)

    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    if not validator.validate(url, params, signature):
        log.warning(
            "Rejected request with bad Twilio signature to %s (expected URL %s, "
            "received %s, params %s)",
            request.url.path,
            url,
            request.url,
            sorted(params),
        )
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def check_rate_limit(sender: str) -> None:
    """Allow RATE_LIMIT_MESSAGES per sender per window, else 429."""
    if not sender:
        return
    now = time.monotonic()
    window = _seen[sender]
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= RATE_LIMIT_MESSAGES:
        log.warning("Rate limit hit for one sender")
        raise HTTPException(status_code=429, detail="Too many messages, please wait")
    window.append(now)
    _sweep(cutoff)


def _sweep(cutoff: float) -> None:
    """Forget senders whose window has emptied.

    Without this every phone number that ever messages leaves an entry
    behind — a slow leak that only shows up after months of real
    traffic, which is exactly when nobody is looking.
    """
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep < SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    stale = [
        sender
        for sender, window in _seen.items()
        if not window or window[-1] < cutoff
    ]
    for sender in stale:
        del _seen[sender]
    if stale:
        log.debug("Rate limiter forgot %d idle senders", len(stale))


def tracked_senders() -> int:
    return len(_seen)


def reset_rate_limits() -> None:
    global _last_sweep
    _seen.clear()
    _last_sweep = 0.0


def security_status() -> dict:
    """Surfaced on the dashboard so an unprotected deployment is
    visible rather than silently accepted."""
    return {
        "twilio_signature_verification": signature_checking_enabled(),
        "rate_limit_per_minute": RATE_LIMIT_MESSAGES,
        "session_ttl_seconds": config.SESSION_TTL_SECONDS,
        "warnings": (
            []
            if signature_checking_enabled()
            else [
                "TWILIO_AUTH_TOKEN is not set — webhook requests are not "
                "verified. Anyone who knows the URL can submit fabricated "
                "triage data. Set it before deploying."
            ]
        ),
    }
