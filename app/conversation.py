"""GPT-4o conversation engine with per-number session memory (Sprint 2)
and structured triage classification (Sprint 3).

Every model turn returns JSON {triage, reason, reply}. A deterministic
red-flag check runs BEFORE the model, so emergency detection never
depends on an API call succeeding.
"""

import logging
import random
import time

from openai import OpenAI

from app import config, facilities, language, observability, rag, records, safety_net, triage
from app.sessions import make_session_store

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are HealthBot NG, a health triage assistant for people in Nigeria, \
reached via WhatsApp.

Rules you must never break:
- You triage and route only. NEVER diagnose a condition and NEVER prescribe \
or dose any medicine.
- Ask exactly ONE short question per message, and decide after at most 4 \
questions.
- Reply in the language the user writes in (English, Nigerian Pidgin, Hausa, \
Yoruba or Igbo). Never ask them to switch language.
- Keep replies short and simple — this is WhatsApp, users may have low \
literacy and small data plans.
- Assume the person has NO medical equipment. Never ask for a \
thermometer reading, blood pressure, weight or any measurement. Ask \
only about what they can see or feel: is the body hot to touch, is the \
child drinking or feeding, is breathing fast or noisy, is the person \
awake and responsive, how many days has it lasted.
- If anything sounds like an emergency (trouble breathing, convulsions, \
unconsciousness, severe bleeding, chest pain, a very weak baby), classify \
EMERGENCY immediately — stop asking questions.
- Fixed emergency rules — never downgrade these to CLINIC: a baby under \
two months who is hot, not feeding, or unusually quiet or weak; any snake \
bite; bleeding during pregnancy; a child who falls then vomits repeatedly \
or becomes very sleepy; vomiting that will not stop.
- SELF_CARE only when mild, recent, no fever, the person eats and drinks \
normally, and no danger sign is present. A mild cold, a small cut, \
tiredness after farm work, or a mild headache with no fever are SELF_CARE \
— do not over-refer. When torn between CLINIC and EMERGENCY choose \
EMERGENCY; when torn between SELF_CARE and CLINIC choose CLINIC only for \
fever, weakness, worsening, a young infant, or an older adult.

Output format — respond ONLY with a JSON object, nothing else:
{"triage": "PENDING" | "SELF_CARE" | "CLINIC" | "EMERGENCY",
 "language": "english" | "pidgin" | "hausa" | "yoruba" | "igbo",
 "reason": "<one short English sentence for the health record>",
 "reply": "<your message to the user, in THEIR language>"}

"language" is the language the user is writing in (your reply must be in
it). If they mix languages, pick the dominant one.

Use "PENDING" while you still need to ask a question. Use a final level the \
moment you have enough information:
- SELF_CARE: safe to manage at home; give simple home guidance.
- CLINIC: should see a health worker today/soon.
- EMERGENCY: go to the nearest facility NOW; say so plainly.
"""

WELCOME = (
    "Welcome to HealthBot NG! 👋 Talk to me in English, Pidgin, Hausa, "
    "Yoruba or Igbo — tell me wetin dey worry you or your family."
)

# Hausa/Yoruba/Igbo texts are draft translations — have native speakers
# verify them before deployment/evaluation.
FALLBACKS = {
    "english": (
        "Sorry, I cannot reply right now — please try again shortly. "
        "If it is serious, do not wait: go to the nearest clinic or "
        "health centre."
    ),
    "pidgin": (
        "Sorry, I no fit reply right now — abeg try again small time. "
        "If the matter serious, no wait: go the nearest clinic or health centre."
    ),
    "hausa": (
        "Yi hakuri, ba zan iya amsawa yanzu ba — sake gwadawa nan gaba "
        "kadan. Idan lamarin yana da tsanani, kada ka jira: je asibiti "
        "ko cibiyar lafiya mafi kusa."
    ),
    "yoruba": (
        "Ma binu, mi o le dahun bayii — gbiyanju leekansi laipe. Ti oro "
        "naa ba le, ma duro: lo si ile-iwosan tabi ile-ise ilera to "
        "sunmo julo."
    ),
    "igbo": (
        "Ndo, enweghị m ike ịza ugbu a — nwaa ọzọ obere oge. Ọ bụrụ na "
        "ọ dị njọ, echela: gaa ụlọ ọgwụ ma ọ bụ ebe ahụike kacha nso."
    ),
}
FALLBACK = FALLBACKS["pidgin"]

# Asked after a CLINIC/EMERGENCY verdict so we can route to a facility.
LOCATION_HINTS = {
    "english": "📍 Share your location (attach → Location) and I will show you the nearest facility.",
    "pidgin": "📍 Share your location (attach → Location) make I show you the nearest place wey fit help.",
    "hausa": "📍 Aiko da wurin da kake (location) don in nuna maka asibiti mafi kusa.",
    "yoruba": "📍 Fi ipo re ranse (location) ki n le fi ile-iwosan to sunmo julo han e.",
    "igbo": "📍 Zipu ebe ị nọ (location) ka m gosi gị ụlọ ọgwụ kacha nso.",
}

RESET_COMMANDS = {"reset", "restart", "start again", "start over"}

store = make_session_store(
    max_messages=config.MAX_HISTORY_MESSAGES,
    ttl_seconds=config.SESSION_TTL_SECONDS,
)


def handle_message(
    from_number: str,
    body: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> str:
    session_id = store.anonymise(from_number)
    text = body.strip()

    # A shared location pin routes to a facility (its body is usually empty).
    if latitude is not None and longitude is not None:
        return _handle_location(session_id, latitude, longitude)

    if not text:
        return WELCOME
    if text.lower() in RESET_COMMANDS:
        store.reset(session_id)
        return WELCOME

    lang = _resolve_language(session_id, text)
    try:
        result = classify_turn(session_id, text, lang)
    except Exception:
        # Twilio must always get a 200 with TwiML; degrade safely and
        # escalate up, never down.
        return FALLBACKS[lang]

    if result.language:
        # The LLM sees the whole conversation — trust it over the heuristic.
        store.set_meta(session_id, "language", result.language)
        lang = result.language
    reply = triage.format_reply(result)
    if result.level != triage.TriageLevel.PENDING:
        records.log_triage(session_id, "whatsapp", lang, result.level.value, result.reason)
    if result.level in (triage.TriageLevel.SELF_CARE, triage.TriageLevel.CLINIC):
        # Triage is a snapshot; illness moves. Anyone going home needs to
        # know which signs mean come back — deterministically, not left
        # to whether the model happened to mention it.
        reply = f"{reply}\n\n{safety_net.advice_block(lang, _is_child(session_id))}"
    if result.level in (triage.TriageLevel.CLINIC, triage.TriageLevel.EMERGENCY):
        store.set_meta(session_id, "last_triage", result.level.value)
        reply = _with_location_hint(reply, lang)
    return reply


def _is_child(session_id: str) -> bool:
    """Whether this conversation is about a child, so the return signs
    match the IMCI age band. Inferred from what has been said, since
    WhatsApp has no structured 'who is sick' step the way USSD does."""
    words = " ".join(m["content"].lower() for m in store.history(session_id))
    return any(
        w in words
        for w in (
            # English / Pidgin
            "child", "baby", "infant", "newborn", "son", "daughter", "pikin",
            "months old", "month old", "weeks old",
            # Hausa / Yoruba / Igbo
            "yaro", "yarona", "yarinya", "jariri", "omo", "omo mi", "nwa", "nwa m",
        )
    )


def classify_turn(session_id: str, text: str, lang: str = "english") -> triage.TriageResult:
    """One turn against the real triage pipeline: red-flag safety net,
    RAG-grounded system prompt, GPT-4o JSON verdict. Updates session
    history; raises on LLM failure (caller decides how to degrade).
    Shared by the webhook path and the Chapter 5 evaluation harness —
    does NOT write triage_records or apply channel formatting."""
    store.append(session_id, "user", text)
    matched = triage.matched_red_flag(text)
    if matched:
        result = triage.emergency_override(lang, matched)
    else:
        messages = [
            {"role": "system", "content": _system_prompt(text)},
            *store.history(session_id),
        ]
        result = triage.parse_triage_response(_chat_completion(messages))
        # Deterministic downgrade guard: the LLM can under-triage
        # (demonstrated on the full vignette corpus), so re-scan the whole
        # conversation for danger signs and refuse unsafe SELF_CARE
        # verdicts. The guard only escalates, never downgrades.
        history_text = " ".join(
            m["content"] for m in store.history(session_id) if m["role"] == "user"
        )
        user_turns = sum(1 for m in store.history(session_id) if m["role"] == "user")
        guarded_level, guard_reason, changed = triage.guard_verdict(
            result.level, history_text, user_turns
        )
        if changed:
            if guarded_level == triage.TriageLevel.EMERGENCY:
                result = triage.emergency_override(
                    lang, triage.matched_red_flag(history_text)
                )
            else:
                result = triage.TriageResult(
                    level=guarded_level,
                    reason=guard_reason,
                    reply=triage.CLINIC_FALLBACKS.get(
                        result.language or lang, triage.CLINIC_FALLBACKS["pidgin"]
                    ),
                    language=result.language,
                )
    store.append(session_id, "assistant", result.reply)
    # Clinical audit, when enabled — a no-op by default.
    records.log_turn(
        session_id,
        turn=len(store.history(session_id)) // 2,
        language=lang,
        user_text=text,
        bot_text=result.reply,
        level=result.level.value,
    )
    return result


def _with_location_hint(reply: str, lang: str) -> str:
    hint = LOCATION_HINTS.get(lang, LOCATION_HINTS["english"])
    return f"{reply}\n\n{hint}"


def _handle_location(session_id: str, latitude: float, longitude: float) -> str:
    lang = store.get_meta(session_id, "language", language.ENGLISH)
    emergency = store.get_meta(session_id, "last_triage") == triage.TriageLevel.EMERGENCY.value
    nearest = facilities.find_nearest(latitude, longitude, emergency=emergency)
    if nearest is None:
        records.log_routing_miss(
            triage.TriageLevel.EMERGENCY.value if emergency else triage.TriageLevel.CLINIC.value,
            channel="whatsapp",
        )
        reply = facilities.no_facility_reply(lang)
    else:
        facility, distance_km = nearest
        # Log the facility, not the patient's coordinates — those are
        # used to choose a facility and then discarded.
        records.log_referral(
            facility,
            distance_km,
            triage.TriageLevel.EMERGENCY.value if emergency else triage.TriageLevel.CLINIC.value,
        )
        reply = facilities.format_facility_reply(facility, distance_km, language=lang)
    store.append(session_id, "assistant", reply)
    return reply


def _resolve_language(session_id: str, text: str) -> str:
    """Heuristic detection, falling back to the session's last known
    language, then English. Only used for deterministic messages —
    GPT-4o handles language natively for its own replies."""
    detected = language.detect_language(text)
    if detected:
        store.set_meta(session_id, "language", detected)
        return detected
    return store.get_meta(session_id, "language", language.ENGLISH)


def _system_prompt(user_text: str) -> str:
    """Base rules, plus retrieved FMOH/WHO protocol excerpts when available."""
    context = rag.format_context(rag.retrieve(user_text))
    if not context:
        return SYSTEM_PROMPT
    return (
        f"{SYSTEM_PROMPT}\n"
        "Clinical protocol excerpts (FMOH/WHO) that may be relevant to this "
        "case:\n\n"
        f"{context}\n\n"
        "Ground your questions and guidance in these excerpts when they "
        "apply. They inform the triage level only — they never justify "
        "diagnosing or prescribing, and they never override the emergency "
        "rules above."
    )


# Optional call parameters that some providers/models reject. Which
# ones are unsupported is discovered at runtime and remembered, so a
# rejected parameter costs one retry per process, not one per message.
# (Claude models via OpenAI-compatible gateways typically refuse
# response_format=json_object; newer GPT models renamed max_tokens to
# max_completion_tokens and fix temperature at 1; older OpenAI-style
# models reject max_completion_tokens. DeepSeek's thinking mode ignores
# temperature. Some models reject both token limits and must be left to
# their own defaults.)
OPTIONAL_PARAMS = ("response_format", "temperature", "max_tokens", "max_completion_tokens")
_unsupported: set[str] = set()


def _client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL or None,
        # The SDK defaults to a 600s read timeout. Twilio abandons a
        # webhook at 15s, so anything past that produces no reply for
        # the patient while still holding a worker thread for ten
        # minutes. Fail fast enough to return the safe fallback.
        timeout=config.LLM_TIMEOUT_SECONDS,
        max_retries=0,  # retries are handled below, with backoff
    )


def _provider_name() -> str:
    base = (config.OPENAI_BASE_URL or "").rstrip("/")
    if not base:
        return "openai"
    return base.split("://")[-1].split("/")[0]


# Transient conditions worth retrying: throttling, gateway hiccups and
# dropped connections. During evaluation these matter more than in
# production — a rate-limited vignette would otherwise be scored as a
# wrong clinical answer, quietly depressing reported accuracy.
_TRANSIENT_MARKERS = (
    "rate limit", "rate_limit", "429", "too many requests",
    "500", "502", "503", "504", "overloaded", "server_error",
    "timeout", "timed out", "connection", "temporarily unavailable",
)


def is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _retry_delay(attempt: int, exc: Exception) -> float:
    """Honour the provider's Retry-After when it gives one; otherwise
    back off exponentially with a little jitter so parallel callers do
    not retry in lockstep."""
    retry_after = getattr(getattr(exc, "response", None), "headers", {}) or {}
    try:
        supplied = float(retry_after.get("retry-after", ""))
        return min(supplied, config.LLM_MAX_BACKOFF_SECONDS)
    except (TypeError, ValueError):
        pass
    return min(2**attempt + random.uniform(0, 0.5), config.LLM_MAX_BACKOFF_SECONDS)


def _completion_kwargs(messages: list[dict]) -> dict:
    kwargs: dict = {"model": config.OPENAI_MODEL, "messages": messages}
    # Provider-specific settings (e.g. a reasoning/mode flag) that are
    # not part of the standard schema. Dropped individually if rejected.
    for name, value in config.OPENAI_EXTRA_PARAMS.items():
        if name not in _unsupported:
            kwargs[name] = value
    if "response_format" not in _unsupported:
        # Belt and braces: the prompt also demands JSON, and the parser
        # tolerates fenced//prose-wrapped JSON, so dropping this is safe.
        kwargs["response_format"] = {"type": "json_object"}
    if "temperature" not in _unsupported:
        kwargs["temperature"] = 0.2
    if "max_tokens" not in _unsupported:
        kwargs["max_tokens"] = 400
    elif "max_completion_tokens" not in _unsupported:
        # Model renamed max_tokens; use the newer name.
        kwargs["max_completion_tokens"] = 400
    # Both names rejected → send no token limit; the provider's default
    # (typically ample) applies rather than failing the request.
    return kwargs


def _mark_unsupported(exc: Exception) -> bool:
    """If the provider rejected an optional parameter, remember it so
    the next attempt omits it. Returns True when a retry is worthwhile."""
    message = str(exc).lower()
    rejection = any(
        word in message
        for word in ("unsupported", "not supported", "unrecognized", "unknown", "invalid")
    )
    if not rejection:
        return False
    for param in (*OPTIONAL_PARAMS, *config.OPENAI_EXTRA_PARAMS):
        if param in message and param not in _unsupported:
            _unsupported.add(param)
            log.warning("Provider rejected '%s'; retrying without it", param)
            return True
    return False


def _chat_completion(messages: list[dict]) -> str:
    """One completion, retrying transient failures.

    Two different retry reasons are handled here and they are not the
    same thing: an unsupported parameter is permanent and is retried
    immediately without it, while throttling is temporary and is
    retried after a wait.
    """
    client = _client()
    param_attempts = len(OPTIONAL_PARAMS) + len(config.OPENAI_EXTRA_PARAMS) + 1
    transient_attempts = 0

    attempt = 0
    for _ in range(param_attempts + config.LLM_MAX_RETRIES):
        attempt += 1
        started = observability.now_ms()
        try:
            response = client.chat.completions.create(**_completion_kwargs(messages))
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
            records.log_ai_event(
                provider=_provider_name(),
                model=config.OPENAI_MODEL,
                duration_ms=int((observability.now_ms() - started) * 1000),
                ok=True,
                attempt=attempt,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=observability.estimate_cost(
                    config.OPENAI_MODEL, prompt_tokens, completion_tokens
                ),
            )
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
            # Some providers intermittently return an empty completion
            # (observed with DeepSeek JSON mode). Treat it like a
            # transient error: retry with backoff, then fail over to the
            # caller's language-safe fallback instead of letting an
            # empty string reach the parser (which would silently
            # escalate to CLINIC with the wrong reason).
            if transient_attempts < config.LLM_MAX_RETRIES:
                delay = _retry_delay(transient_attempts, RuntimeError("empty model output"))
                transient_attempts += 1
                log.warning(
                    "Empty LLM output; retry %d/%d in %.1fs",
                    transient_attempts, config.LLM_MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise RuntimeError("Empty model output after retries")
        except Exception as exc:
            records.log_ai_event(
                provider=_provider_name(),
                model=config.OPENAI_MODEL,
                duration_ms=int((observability.now_ms() - started) * 1000),
                ok=False,
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            if _mark_unsupported(exc):
                continue
            if is_transient(exc) and transient_attempts < config.LLM_MAX_RETRIES:
                delay = _retry_delay(transient_attempts, exc)
                transient_attempts += 1
                log.warning(
                    "Transient LLM error (%s); retry %d/%d in %.1fs",
                    type(exc).__name__, transient_attempts, config.LLM_MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("No working parameter combination for this model")
