"""Console authentication.

The console has no user accounts; it is protected by the shared
ADMIN_TOKEN. Login exchanges the token for a short-lived, HttpOnly,
SameSite=Lax session cookie whose signature is keyed by the token, so
rotating ADMIN_TOKEN immediately invalidates every issued session.

Webhooks, the participant survey and the public SUS submission must
remain unauthenticated, so this module is only applied to the console
API routes via middleware in app/main.py.
"""

import hashlib
import hmac
import time
from fastapi import Request, Response, HTTPException

from app import config

SESSION_COOKIE = "healthbot_session"
SESSION_TTL_SECONDS = 12 * 3600


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _sign(payload: str) -> str:
    return hmac.new(
        config.ADMIN_TOKEN.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def create_session_cookie() -> str:
    """A signed session value for the current ADMIN_TOKEN."""
    payload = f"{_fingerprint(config.ADMIN_TOKEN)}.{int(time.time()) + SESSION_TTL_SECONDS}"
    return f"{payload}.{_sign(payload)}"


def verify_session(value: str) -> bool:
    """Valid, unexpired session signed by the current ADMIN_TOKEN."""
    if not config.ADMIN_TOKEN or not value:
        return False
    try:
        fingerprint, expiry, signature = value.split(".", 2)
    except ValueError:
        return False
    payload = f"{fingerprint}.{expiry}"
    if not hmac.compare_digest(signature, _sign(payload)):
        return False
    if fingerprint != _fingerprint(config.ADMIN_TOKEN):
        return False  # token was rotated
    return int(expiry) > time.time()


def console_authenticated(request: Request) -> bool:
    return verify_session(request.cookies.get(SESSION_COOKIE, ""))


def set_session_cookie(response: Response) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_cookie(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def require_console_auth(request: Request) -> None:
    if not console_authenticated(request):
        raise HTTPException(status_code=401, detail="Console login required")
