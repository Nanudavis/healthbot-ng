"""Runtime LLM settings, changeable from the admin console.

Why this is guarded: the dashboard has no user accounts, so an endpoint
that accepts an API key would let anyone who reaches the console spend
the project's credits or redirect traffic to a provider of their
choosing. Writes therefore require ADMIN_TOKEN, and when that is unset
they are refused outright rather than allowed — an unconfigured system
must be locked, not open.

Secrets are never returned to the browser; reads expose only the last
four characters, enough to tell which key is loaded.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app import config, db
from app.models import AppSetting

log = logging.getLogger(__name__)

# Settings an administrator may change. Anything not listed is ignored,
# so a crafted request cannot reach unrelated configuration.
EDITABLE = {
    "OPENAI_API_KEY": {"secret": True, "label": "API key"},
    "OPENAI_BASE_URL": {"secret": False, "label": "Base URL"},
    "OPENAI_MODEL": {"secret": False, "label": "Model"},
    "EMBEDDING_PROVIDER": {"secret": False, "label": "Embeddings"},
}

# Presets mirroring scripts/use_provider.py, so the console offers the
# same known-good combinations rather than free-typing three fields.
PRESETS = {
    "openai": {
        "label": "OpenAI (direct)",
        "OPENAI_BASE_URL": "",
        "OPENAI_MODEL": "gpt-4o",
        "EMBEDDING_PROVIDER": "openai",
        "key_hint": "sk-…",
        "note": "OpenAI serves chat and embeddings, so RAG can keep using API embeddings.",
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "agentrouter": {
        "label": "AgentRouter",
        "OPENAI_BASE_URL": "https://agentrouter.org/v1",
        "OPENAI_MODEL": "gpt-5.6",
        "EMBEDDING_PROVIDER": "local",
        "key_hint": "sk-…",
        "note": "AgentRouter serves chat models only — embeddings switch to the on-device model automatically.",
        "models": ["gpt-5.6", "gpt-5.5", "claude-opus-4-8", "claude-opus-4-7", "glm-5.2"],
    },
    "deepseek": {
        "label": "DeepSeek (V4 Flash)",
        "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
        "OPENAI_MODEL": "deepseek-v4-flash",
        "EMBEDDING_PROVIDER": "local",
        "key_hint": "sk-…",
        "note": (
            "DeepSeek serves chat models only — embeddings switch to the on-device "
            "model automatically. Key from platform.deepseek.com. V4 Flash is the "
            "fast, cheap default; V4 Pro trades cost for capability."
        ),
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
}


class NotAuthorised(Exception):
    """Raised when a write is attempted without a valid admin token."""


def writes_enabled() -> bool:
    return bool(config.ADMIN_TOKEN)


def check_token(token: str) -> None:
    import hmac

    if not writes_enabled():
        raise NotAuthorised(
            "ADMIN_TOKEN is not set, so settings cannot be changed from the "
            "console. Set it in .env and restart."
        )
    if not hmac.compare_digest(token or "", config.ADMIN_TOKEN):
        raise NotAuthorised("Invalid admin token.")


def load_into_config() -> int:
    """Apply stored settings over the .env defaults. Called at startup so
    an administrator's choice survives a restart."""
    try:
        db.init_db()
        with db.get_session() as session:
            rows = session.scalars(select(AppSetting)).all()
            stored = {r.key: r.value for r in rows}
    except Exception:
        log.warning("Could not load stored settings; using .env values")
        return 0
    applied = 0
    for key, value in stored.items():
        # A stored blank is meaningful for non-secret keys: it means
        # "cleared to default" (e.g. base URL back to OpenAI direct).
        # Blank secrets keep meaning "leave the key alone" and are skipped.
        if key in EDITABLE and (value or not EDITABLE[key]["secret"]):
            setattr(config, key, value)
            applied += 1
    return applied


def current() -> dict:
    """Settings as they are now, with secrets masked."""
    values = {}
    for key, meta in EDITABLE.items():
        raw = getattr(config, key, "") or ""
        values[key] = {
            "label": meta["label"],
            "value": f"…{raw[-4:]}" if meta["secret"] and raw else ("" if meta["secret"] else raw),
            "is_set": bool(raw),
            "secret": meta["secret"],
        }
    provider = "custom"
    base_url = getattr(config, "OPENAI_BASE_URL", "") or ""
    for name, preset in PRESETS.items():
        if preset["OPENAI_BASE_URL"] == base_url:
            provider = name
            break
    return {
        "settings": values,
        "provider": provider,
        "presets": PRESETS,
        "writes_enabled": writes_enabled(),
        "note": (
            None
            if writes_enabled()
            else "Set ADMIN_TOKEN in .env to enable changes from this page."
        ),
    }


def update(changes: dict, token: str) -> dict:
    """Apply and persist settings. Requires a valid admin token."""
    check_token(token)

    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")

    db.init_db()
    applied = []
    with db.get_session() as session:
        for key, value in changes.items():
            value = (value or "").strip()
            # A blank secret means "leave the existing key alone", so an
            # administrator editing the model does not wipe the key.
            if EDITABLE[key]["secret"] and not value:
                continue
            row = session.scalar(select(AppSetting).where(AppSetting.key == key))
            if row:
                row.value = value
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(AppSetting(key=key, value=value))
            setattr(config, key, value)
            applied.append(key)
        session.commit()

    if applied:
        # Different models accept different parameters; forget what the
        # previous one rejected.
        from app import conversation, rag

        conversation._unsupported.clear()
        # Embedding-relevant config changed: the cached vector store (and
        # its embedding client) belongs to the old settings. Drop it so the
        # next retrieval re-initialises with the new provider/key. If the
        # index itself was built with a different embedding model, the
        # knowledge page now warns and asks for a rebuild.
        if set(applied) & {"OPENAI_API_KEY", "OPENAI_BASE_URL", "EMBEDDING_PROVIDER"}:
            rag.reset_store()
        log.info("Settings updated: %s", ", ".join(applied))
    return {"applied": applied, **current()}


def apply_preset(name: str, token: str) -> dict:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset {name!r}")
    preset = PRESETS[name]
    return update(
        {
            "OPENAI_BASE_URL": preset["OPENAI_BASE_URL"],
            "OPENAI_MODEL": preset["OPENAI_MODEL"],
            "EMBEDDING_PROVIDER": preset["EMBEDDING_PROVIDER"],
        },
        token,
    )


def test_connection() -> dict:
    """Test chat AND embeddings with the current settings.

    Chat and embeddings can live on different providers (many gateways
    proxy chat models but no embedding models), so both are verified and
    reported separately — a green chat test alone can hide a broken RAG
    stack that only surfaces in triage.
    """
    from app import conversation, rag

    # DeepSeek (and other OpenAI-compatible providers) require the literal
    # word "json" in the prompt when response_format=json_object is used —
    # a probe that omits it would report a working key as broken.
    try:
        raw = conversation._chat_completion(
            [{"role": "user", "content": 'Reply with a single JSON object, for example: {"ok": true}'}]
        )
        chat = {"ok": True, "model": config.OPENAI_MODEL, "sample": raw[:200]}
    except Exception as exc:
        chat = {
            "ok": False,
            "model": config.OPENAI_MODEL,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }

    if config.EMBEDDING_PROVIDER == "local":
        embeddings = {
            "ok": True,
            "provider": "local",
            "note": "on-device embeddings — no API call needed",
        }
    else:
        try:
            rag._embeddings().embed_query("child fever danger signs")
            embeddings = {"ok": True, "provider": "openai", "model": config.EMBEDDING_MODEL}
        except Exception as exc:
            embeddings = {
                "ok": False,
                "provider": "openai",
                "model": config.EMBEDDING_MODEL,
                "error": f"{type(exc).__name__}: {exc}"[:200],
            }

    return {**chat, "embeddings": embeddings}
