"""Lightweight observability: request IDs, structured JSON logs, and
per-call LLM latency/token/cost events.

Design goals (from the engineering audit):
- Every HTTP request gets an X-Request-ID that is carried through logs.
- App logs are structured JSON lines with request_id and event fields.
- Every LLM API call records an anonymised AiEvent (model, duration,
  tokens, estimated cost, outcome) — no prompt/reply text is ever
  stored, and the write is fail-safe like the rest of the data layer.
"""

import contextvars
import json
import logging
import time
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def current_request_id() -> str:
    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def set_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class HealthbotJsonFormatter(logging.Formatter):
    """One JSON object per log line, with request_id and event fields."""

    _EXTRA = (
        "duration_ms",
        "status",
        "method",
        "path",
        "model",
        "provider",
        "attempt",
        "error_type",
        "prompt_tokens",
        "completion_tokens",
        "estimated_cost_usd",
    )

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "event": getattr(record, "event", record.getMessage()),
        }
        for key in self._EXTRA:
            value = getattr(record, key, None)
            if value is not None:
                data[key] = value
        return json.dumps(data)


def _attach(logger_name: str, propagate: bool) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.addFilter(RequestIdFilter())
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_healthbot", False)
        for h in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(HealthbotJsonFormatter())
        handler._healthbot = True
        logger.addHandler(handler)
    logger.propagate = propagate
    return logger


app_logger: logging.Logger = None
request_logger: logging.Logger = None


def setup_logging() -> None:
    """Configure structured logging for app modules and HTTP requests."""
    global app_logger, request_logger
    app_logger = _attach("app", propagate=False)
    request_logger = _attach("healthbot.request", propagate=False)


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """Emit a structured event line with request_id already attached."""
    logger.info(event, extra=fields)


# Approximate published list prices per 1M tokens (USD), keyed by model
# name. Unknown models produce a NULL cost rather than a guessed one.
_COST_PER_1M = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}


def estimate_cost(model: str, prompt_tokens, completion_tokens) -> float | None:
    """Estimated USD cost for one call, or None when unknown/unmeasurable."""
    prices = _COST_PER_1M.get(model)
    if prices is None or not prompt_tokens or not completion_tokens:
        return None
    in_price, out_price = prices
    return round(
        (prompt_tokens / 1_000_000) * in_price
        + (completion_tokens / 1_000_000) * out_price,
        8,
    )


def now_ms() -> float:
    return time.perf_counter()
