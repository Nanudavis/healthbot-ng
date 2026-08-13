import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# A blank line in .env (e.g. "OPENAI_BASE_URL=") is exported as an empty
# string, and the OpenAI SDK reads these variables directly — treating
# "" as a real base URL and failing with "missing an http:// protocol".
# Drop blanks so the SDK's own defaults apply.
for _blank in ("OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
    if not os.environ.get(_blank, "").strip():
        os.environ.pop(_blank, None)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
# Any OpenAI-compatible endpoint (OpenAI itself, or a third-party
# gateway serving Claude / GPT / other models). Empty = OpenAI direct.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")


def _extra_params() -> dict:
    """Provider-specific chat parameters, as a JSON object.

    Some models expose a mode/effort setting that is not part of the
    standard OpenAI schema, e.g. {"reasoning_effort": "high"} or
    {"mode": "sol"}. Whatever your provider documents goes here and is
    merged into every chat call.
    """
    raw = os.getenv("OPENAI_EXTRA_PARAMS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("OPENAI_EXTRA_PARAMS is not valid JSON — ignoring it")
        return {}
    if not isinstance(parsed, dict):
        log.warning("OPENAI_EXTRA_PARAMS must be a JSON object — ignoring it")
        return {}
    return parsed


OPENAI_EXTRA_PARAMS = _extra_params()

# "local" runs a sentence-transformer on this machine (no API, no cost,
# works offline). Anything else is treated as a remote OpenAI-style
# embedding model name.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()
# Twilio abandons a webhook at 15s, so the model call must fail well
# before that and let the safe fallback reply go out.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))
# Throttling is temporary. Without retries a rate-limited vignette is
# scored as a wrong clinical answer, which would understate accuracy in
# the evaluation rather than reporting an infrastructure problem.
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_MAX_BACKOFF_SECONDS = float(os.getenv("LLM_MAX_BACKOFF_SECONDS", "20"))

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
# Multilingual by design: the corpus is English clinical text but
# queries arrive in Pidgin/Hausa/Yoruba.
LOCAL_EMBEDDING_MODEL = os.getenv(
    "LOCAL_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
# Embeddings often live on a different provider than chat — many
# gateways proxy chat models but not embeddings. Blank = reuse the
# chat key/endpoint above.
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")

# "hf" runs the same sentence-transformer model on Hugging Face's hosted
# Inference API instead of on this machine — no torch/RAM cost, same
# vector space as a locally built index (client-side L2-normalised).
HF_API_TOKEN = os.getenv("HF_API_TOKEN", "")
# bge-small-en-v1.5: HF's hosted inference serves it as raw
# feature-extraction (384-dim). The multilingual MiniLM model is only
# exposed through HF's sentence-similarity pipeline, which cannot return
# embeddings, so the cloud index is built with bge instead.
HF_EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL",
    "BAAI/bge-small-en-v1.5",
)

# RAG (Sprint 4): Pinecone in production, local JSON index for offline dev.
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "healthbot-protocols")
PROTOCOLS_DIR = os.getenv("PROTOCOLS_DIR", "data/protocols")
LOCAL_INDEX_PATH = os.getenv("LOCAL_INDEX_PATH", "data/index/protocols.local.json")
RAG_TOP_K = 4

# IDSR-style alert thresholds for the surveillance console. A series
# "alerts" when the trailing window holds at least ALERT_MIN_COUNT
# reports AND at least ALERT_MULTIPLIER × the previous equal-length
# window (zero previous reports counts as a new signal once the minimum
# is met). These are community-signal rules, not official thresholds.
ALERT_WINDOW_DAYS = int(os.getenv("ALERT_WINDOW_DAYS", "14"))
ALERT_MIN_COUNT = int(os.getenv("ALERT_MIN_COUNT", "5"))
ALERT_MULTIPLIER = float(os.getenv("ALERT_MULTIPLIER", "2.0"))

# ── Webhook security ──
# Twilio signs every request with the account auth token. Without this
# set, signature verification is skipped — fine locally, unsafe in
# deployment, and reported as a warning on the dashboard.
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
# Behind a tunnel or proxy the URL the app sees differs from the one
# Twilio signed. Set this to the public origin (e.g. https://xyz.ngrok-free.dev)
# so the signature is checked against the URL Twilio actually used.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

# Conversations expire after this much inactivity, so a stale complaint
# is never triaged as though it were current.
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
# "memory" = process-local dict (current default; single worker).
# "db" = SQLAlchemy-backed store so sessions survive restarts and work
# across workers. The DB store persists transient conversation text, so
# it is opt-in and TTL-purged (see app/sessions.py).
SESSION_STORE = os.getenv("SESSION_STORE", "memory").strip().lower()

# Console authentication. When enabled, all console API routes require a
# session cookie obtained from /api/auth/login with the ADMIN_TOKEN.
# Webhooks, /health, /survey and the public SUS submission stay open.
CONSOLE_AUTH_REQUIRED = os.getenv("CONSOLE_AUTH_REQUIRED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# ── Async outbound (WhatsApp) ───────────────────────────────────
# When enabled, the WhatsApp webhook acknowledges immediately (empty
# TwiML) and a background worker runs the triage pipeline and sends the
# reply via the Twilio REST API. Requires TWILIO_ACCOUNT_SID,
# TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER; without them the webhook
# falls back to the synchronous TwiML reply.
WHATSAPP_ASYNC_OUTBOUND = os.getenv("WHATSAPP_ASYNC_OUTBOUND", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
OUTBOUND_MAX_ATTEMPTS = int(os.getenv("OUTBOUND_MAX_ATTEMPTS", "3"))
OUTBOUND_POLL_SECONDS = float(os.getenv("OUTBOUND_POLL_SECONDS", "1"))
OUTBOUND_RETRY_BASE_SECONDS = float(os.getenv("OUTBOUND_RETRY_BASE_SECONDS", "5"))

# Clinical audit of real conversations. OFF by default: transcripts are
# the patient's own words and may contain identifying details they typed
# unprompted, which the no-PII rule forbids storing. Enable only with an
# ethics approval that covers it; text is scrubbed either way.
STORE_TRANSCRIPTS = os.getenv("STORE_TRANSCRIPTS", "").strip().lower() in ("1", "true", "yes")

# Required before the admin console may change the model or API key.
# Unset means settings are read-only there — an unconfigured system is
# locked rather than open, since the console has no user accounts.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Data layer: SQLite for development, PostgreSQL in deployment
# (e.g. postgresql+psycopg://user:pass@host/healthbot).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/healthbot.db")

# Per-session history cap: enough for a full triage conversation,
# small enough to keep prompts cheap.
MAX_HISTORY_MESSAGES = 20
