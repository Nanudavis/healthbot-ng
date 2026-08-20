"""HealthBot NG — multilingual AI health triage for Nigeria.

Sprint 1: WhatsApp webhook via Twilio sandbox (TwiML in/out).
Sprint 2: GPT-4o multi-turn conversation with per-number session memory.
"""

import csv
import hmac
import io
import json
import logging
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from twilio.twiml.messaging_response import MessagingResponse

from app import (
    auth,
    config,
    conversation,
    db,
    knowledge,
    models,
    observability,
    outbound,
    rag,
    records,
    review_items,
    security,
    settings,
    sus,
    ussd,
    vignettes,
)

DISCLAIMER = outbound.DISCLAIMER
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the RAG index off the request path.

    A cold vector-store load takes ~15s, which exceeds Twilio's webhook
    timeout — so the first patient message would time out. Done in a
    background thread so the server accepts traffic immediately;
    retrieval fails open until the store is ready.
    """
    # An administrator's stored choice of model/key must survive a
    # restart, so apply it over the .env defaults before serving.
    settings.load_into_config()
    if config.SEED_SAMPLE_FACILITIES:
        from app import facilities

        if facilities.count_facilities() == 0:
            try:
                n = facilities.seed_facilities(
                    str(Path(config.PROTOCOLS_DIR).parent / "facilities.csv")
                )
                log.info("Seeded %d sample facilities", n)
            except Exception:
                log.warning("Sample facilities seed failed", exc_info=True)
    threading.Thread(target=rag.warm, daemon=True).start()
    outbound_stop = threading.Event()
    worker = None
    if config.WHATSAPP_ASYNC_OUTBOUND and outbound.outbound_available():
        worker = threading.Thread(
            target=outbound.run_worker, args=(outbound_stop,), daemon=True
        )
        worker.start()
    yield
    outbound_stop.set()
    if worker:
        worker.join(timeout=2)


app = FastAPI(title="HealthBot NG", version="0.2.0", lifespan=lifespan)
observability.setup_logging()


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Assign an X-Request-ID, carry it through logs, and emit one
    structured line per HTTP request."""
    request_id = request.headers.get("X-Request-ID") or observability.new_request_id()
    token = observability.set_request_id(request_id)
    started = observability.now_ms()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        observability.log_event(
            observability.request_logger,
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=int((observability.now_ms() - started) * 1000),
            request_id=request_id,
        )
        return response
    except Exception:
        observability.log_event(
            observability.request_logger,
            "http_request",
            method=request.method,
            path=request.url.path,
            status=500,
            duration_ms=int((observability.now_ms() - started) * 1000),
            request_id=request_id,
        )
        raise
    finally:
        observability.reset_request_id(token)

# ── Console authentication middleware ──────────────────────────
# Webhooks, /health, /survey, the public SUS submission and the auth
# endpoints stay open; every other /api route requires a session cookie
# obtained from /api/auth/login with the ADMIN_TOKEN.
_PUBLIC_API = {"/api/auth/login", "/api/auth/logout", "/api/auth/status"}


@app.middleware("http")
async def console_auth_middleware(request: Request, call_next):
    path = request.url.path
    if (
        config.CONSOLE_AUTH_REQUIRED
        and path.startswith("/api/")
        and path not in _PUBLIC_API
        and not (path == "/api/sus" and request.method == "POST")
        and not (path == "/api/native-review" and request.method == "POST")
    ):
        if not auth.console_authenticated(request):
            return JSONResponse(status_code=401, content={"detail": "Console login required"})
    return await call_next(request)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "healthbot-ng"}


_INDEX_TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    """Table of contents: every surface of the system, one link away."""
    return HTMLResponse(_INDEX_TEMPLATE.read_text(encoding="utf-8"))


@app.post("/api/auth/login")
def console_login(token: str = Form("")) -> JSONResponse:
    """Exchange the ADMIN_TOKEN for a short-lived console session cookie."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Console authentication is not configured (set ADMIN_TOKEN)",
        )
    if not hmac.compare_digest(token or "", config.ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid admin token")
    response = JSONResponse({"ok": True})
    auth.set_session_cookie(response)
    return response


@app.post("/api/auth/logout")
def console_logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    auth.clear_session_cookie(response)
    return response


@app.get("/api/auth/status")
def console_auth_status(request: Request) -> dict:
    return {
        "authenticated": (
            not config.CONSOLE_AUTH_REQUIRED or auth.console_authenticated(request)
        ),
        "writes_enabled": bool(config.ADMIN_TOKEN),
    }


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    Latitude: str = Form(""),
    Longitude: str = Form(""),
    MessageSid: str = Form(""),
    SmsMessageSid: str = Form(""),
) -> Response:
    """Twilio WhatsApp webhook: form-encoded in, TwiML XML out.
    Latitude/Longitude arrive when the user shares a location pin."""
    await security.verify_twilio_signature(request)
    # Idempotency: Twilio retries timed-out webhooks; replay the stored
    # response so the triage pipeline and the patient are not hit twice.
    message_sid = (MessageSid or SmsMessageSid or "").strip()
    if config.WHATSAPP_ASYNC_OUTBOUND and outbound.outbound_available():
        # Async mode: acknowledge immediately; the worker runs triage and
        # sends the reply via the Twilio REST API. Queue idempotency by
        # MessageSid handles webhook retries, so nothing is stored here.
        outbound.enqueue(
            to_number=From,
            body=Body,
            latitude=_to_float(Latitude),
            longitude=_to_float(Longitude),
            message_sid=message_sid or None,
        )
        return Response(content=str(MessagingResponse()), media_type="application/xml")
    if message_sid:
        existing = security.message_reply(message_sid)
        if existing is not None:
            return Response(content=existing, media_type="application/xml")
    security.check_rate_limit(From)
    reply = conversation.handle_message(
        From, Body, _to_float(Latitude), _to_float(Longitude)
    )
    twiml = MessagingResponse()
    message = twiml.message(f"{reply}\n\n_{DISCLAIMER}_")
    xml = str(twiml)
    if message_sid:
        security.remember_message(message_sid, xml)
    return Response(content=xml, media_type="application/xml")


@app.post("/webhook/ussd")
def ussd_webhook(
    sessionId: str = Form(""),
    serviceCode: str = Form(""),
    phoneNumber: str = Form(""),
    text: str = Form(""),
) -> PlainTextResponse:
    """Africa's Talking USSD webhook: form-encoded in, CON/END text out.

    Africa's Talking does not sign requests the way Twilio does, so the
    protection here is the rate limit plus the flow being deterministic
    — a forged request cannot reach the LLM or spend anything.
    """
    security.check_rate_limit(phoneNumber)
    return PlainTextResponse(ussd.handle_ussd(sessionId, phoneNumber, text))


@app.get("/api/security/status")
def security_status() -> dict:
    """Whether the deployment's protections are actually switched on."""
    status = security.security_status()
    status["active_sessions"] = conversation.store.active_count()
    return status


@app.get("/api/observability/ai-events")
def ai_events(limit: int = 50) -> list[dict]:
    """Recent LLM calls: model, duration, tokens, estimated cost, outcome.
    Anonymised — no prompt or reply text is stored."""
    return records.ai_events(min(max(limit, 1), 200))


@app.get("/api/observability/outbound")
def outbound_messages(limit: int = 50) -> list[dict]:
    """Recent queued WhatsApp replies and their delivery status."""
    return outbound.outbound_rows(min(max(limit, 1), 200))


@app.get("/api/observability/migrations")
def migrations_status() -> dict:
    """Schema migrations applied to the current database."""
    from app import migrations

    return {"applied": migrations.applied_migrations()}


# ── Surveillance dashboard API (anonymised aggregates only) ─────

@app.get("/api/stats/summary")
def stats_summary(days: int = 0) -> dict:
    """days=0 means all time; every surveillance view takes the same
    window so the dashboard can apply one selector across pages."""
    return records.summary(days or None)


@app.get("/api/stats/daily")
def stats_daily(days: int = 7) -> list[dict]:
    return records.daily(min(days, 90))


@app.get("/api/stats/recent")
def stats_recent(limit: int = 10) -> list[dict]:
    return records.recent(min(limit, 50))


@app.get("/api/stats/symptoms")
def stats_symptoms(days: int = 0) -> list[dict]:
    return records.symptom_trends(days or None)


@app.get("/api/stats/symptom-series")
def stats_symptom_series(days: int = 30) -> dict:
    """Per-day counts per symptom category — the outbreak view."""
    return records.symptom_series(min(max(days, 7), 180))


@app.get("/api/stats/languages")
def stats_languages(days: int = 0) -> list[dict]:
    return records.language_breakdown(days or None)


@app.get("/api/stats/geography")
def stats_geography(days: int = 0) -> list[dict]:
    return records.geography(days or None)


@app.get("/api/stats/facilities")
def stats_facilities(days: int = 0) -> list[dict]:
    return records.facility_routing(days or None)


@app.get("/api/stats/routing-misses")
def stats_routing_misses(days: int = 0) -> dict:
    """Coverage gaps — referrals that found no facility. Coordinates are
    never stored, so this is counts by triage level only."""
    return records.routing_misses(days or None)


@app.get("/api/stats/alerts")
def stats_alerts(days: int = 0) -> dict:
    """IDSR-style threshold checks on a trailing window."""
    return records.alerts(days or config.ALERT_WINDOW_DAYS)


# ── Admin settings (LLM provider, model, API key) ───────────────

@app.get("/api/settings")
def get_settings() -> dict:
    """Current settings; secrets masked to the last four characters."""
    return settings.current()


@app.post("/api/settings")
def update_settings(
    admin_token: str = Form(""),
    preset: str = Form(""),
    OPENAI_API_KEY: str = Form(""),
    OPENAI_BASE_URL: str = Form(None),
    OPENAI_MODEL: str = Form(None),
    EMBEDDING_PROVIDER: str = Form(None),
) -> dict:
    try:
        if preset:
            return settings.apply_preset(preset, admin_token)
        changes = {
            k: v
            for k, v in {
                "OPENAI_API_KEY": OPENAI_API_KEY,
                "OPENAI_BASE_URL": OPENAI_BASE_URL,
                "OPENAI_MODEL": OPENAI_MODEL,
                "EMBEDDING_PROVIDER": EMBEDDING_PROVIDER,
            }.items()
            if v is not None
        }
        return settings.update(changes, admin_token)
    except settings.NotAuthorised as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/settings/test")
def test_settings(admin_token: str = Form("")) -> dict:
    """Make one real LLM call with the current settings."""
    try:
        settings.check_token(admin_token)
    except settings.NotAuthorised as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return settings.test_connection()


# ── Knowledge base (clinical protocol documents behind RAG) ─────

@app.get("/api/knowledge")
def knowledge_status() -> dict:
    """Documents in the corpus and what retrieval is serving."""
    status = knowledge.index_status()
    status["documents"] = knowledge.list_documents()  # replaces the count
    return status


@app.post("/api/knowledge/upload")
async def knowledge_upload(
    admin_token: str = Form(""), file: UploadFile = File(...)
) -> dict:
    """Add a protocol document. Changes what triage is grounded in, so
    it is admin-gated like the model settings."""
    try:
        settings.check_token(admin_token)
    except settings.NotAuthorised as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    try:
        return knowledge.save_upload(file.filename, await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/knowledge/delete")
def knowledge_delete(admin_token: str = Form(""), name: str = Form(...)) -> dict:
    try:
        settings.check_token(admin_token)
    except settings.NotAuthorised as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    try:
        removed = knowledge.delete_document(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail=f"No document named {name}")
    return {"removed": name}


@app.post("/api/knowledge/rebuild")
def knowledge_rebuild(admin_token: str = Form("")) -> dict:
    """Re-embed the corpus in the background; retrieval keeps serving
    the previous index until the new one is ready."""
    try:
        settings.check_token(admin_token)
    except settings.NotAuthorised as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return knowledge.rebuild_async()


@app.get("/api/knowledge/preview")
def knowledge_preview(q: str, k: int = 4) -> list[dict]:
    """What the engine would retrieve for a question."""
    return knowledge.preview(q, min(max(k, 1), 10))


# ── SUS usability study ─────────────────────────────────────────

_SURVEY_TEMPLATE = Path(__file__).resolve().parent / "templates" / "survey.html"


@app.get("/survey", response_class=HTMLResponse)
def survey_form() -> HTMLResponse:
    """Participant-facing SUS questionnaire (mobile-first: participants
    arrive from WhatsApp on a phone)."""
    html = _SURVEY_TEMPLATE.read_text(encoding="utf-8").replace(
        "__ITEMS__", json.dumps(list(sus.ITEMS))
    )
    return HTMLResponse(html)


@app.post("/api/sus")
def submit_sus(
    participant_code: str = Form(...),
    answers: str = Form(...),
    language: str = Form("english"),
    channel: str = Form("whatsapp"),
    comments: str = Form(""),
) -> dict:
    try:
        parsed = [int(a) for a in answers.split(",") if a.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="answers must be 10 numbers, 1-5")
    try:
        return sus.record(participant_code, parsed, language, channel, comments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/sus/summary")
def sus_summary() -> dict:
    return sus.summary()


@app.get("/api/export/sus.csv")
def export_sus_csv() -> Response:
    data = sus.summary()
    buffer = io.StringIO()
    columns = ["participant_code", "language", "channel", "score", "created_at"] + [
        f"q{i}" for i in range(1, 11)
    ] + ["comments"]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for r in data["responses"]:
        row = {
            "participant_code": r["participant_code"],
            "language": r["language"],
            "channel": r["channel"],
            "score": r["score"],
            "created_at": r["created_at"],
            "comments": r["comments"],
        }
        row.update({f"q{i + 1}": a for i, a in enumerate(r["answers"])})
        writer.writerow(row)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="sus_responses.csv"'},
    )


# ── Native-speaker validation of draft translations ─────────────

_NATIVE_TEMPLATE = Path(__file__).resolve().parent / "templates" / "native_review.html"
_REVIEW_LANGS = {"hausa", "yoruba", "igbo"}


@app.get("/native-review", response_class=HTMLResponse)
def native_review_form(language: str = "") -> HTMLResponse:
    """Public, phone-first form for the three language validators."""
    lang = language if language in _REVIEW_LANGS else "hausa"
    html = (
        _NATIVE_TEMPLATE.read_text(encoding="utf-8")
        .replace("__REVIEW_ITEMS__", json.dumps(review_items.items_for(lang)))
        .replace("__MARKERS__", json.dumps(review_items.markers(lang)))
        .replace("__LANG__", lang)
    )
    return HTMLResponse(html)


@app.post("/api/native-review")
def submit_native_review(
    language: str = Form(...),
    reviewer_name: str = Form(...),
    reviewer_role: str = Form(""),
    organisation: str = Form(""),
    assessment: str = Form(""),
    comments: str = Form(""),
    items: str = Form(...),
) -> dict:
    """Store one reviewer's verdicts (public: reviewers get a link)."""
    if language not in _REVIEW_LANGS:
        raise HTTPException(status_code=400, detail="language must be hausa, yoruba or igbo")
    if not reviewer_name.strip():
        raise HTTPException(status_code=400, detail="reviewer_name is required")
    try:
        parsed = json.loads(items)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="items must be valid JSON")
    if not isinstance(parsed, list) or not parsed:
        raise HTTPException(status_code=400, detail="items must be a non-empty list")
    rows = []
    for it in parsed:
        if not isinstance(it, dict):
            raise HTTPException(status_code=400, detail="each item must be an object")
        verdict = str(it.get("verdict", ""))
        if verdict not in ("ok", "correction"):
            raise HTTPException(status_code=400, detail="verdict must be ok or correction")
        if verdict == "correction" and not str(it.get("correction", "")).strip():
            raise HTTPException(
                status_code=400, detail="correction text is required when verdict is correction"
            )
        rows.append(models.NativeReview(
            language=language,
            reviewer_name=reviewer_name.strip(),
            reviewer_role=reviewer_role.strip(),
            organisation=organisation.strip(),
            assessment=assessment.strip(),
            comments=comments.strip(),
            item_id=str(it.get("item_id", ""))[:60],
            item_type=str(it.get("item_type", "string"))[:16],
            english=str(it.get("english", ""))[:4000],
            draft=str(it.get("draft", ""))[:4000],
            verdict=verdict,
            correction=str(it.get("correction", "")).strip()[:4000],
        ))
    session = db.get_session()
    try:
        session.add_all(rows)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return {"status": "saved", "count": len(rows)}


@app.get("/api/export/native-review.csv")
def export_native_reviews() -> Response:
    """Console-gated CSV of every verdict (middleware enforces auth)."""
    session = db.get_session()
    try:
        rows = session.query(models.NativeReview).order_by(
            models.NativeReview.created_at, models.NativeReview.id
        ).all()
    finally:
        session.close()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "created_at", "language", "reviewer_name", "reviewer_role", "organisation",
        "assessment", "comments", "item_id", "item_type", "verdict", "correction",
        "english", "draft",
    ])
    for r in rows:
        writer.writerow([
            r.created_at.isoformat(), r.language, r.reviewer_name, r.reviewer_role,
            r.organisation, r.assessment, r.comments, r.item_id, r.item_type,
            r.verdict, r.correction, r.english, r.draft,
        ])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="native_reviews.csv"'},
    )


# ── Clinical validation of evaluation vignettes ─────────────────

@app.get("/api/vignettes")
def list_vignettes() -> list[dict]:
    return vignettes.list_all()


@app.get("/api/vignettes/progress")
def vignette_progress() -> dict:
    return vignettes.progress()


@app.post("/api/vignettes/import")
async def import_vignettes(file: UploadFile = File(...)) -> dict:
    """Upload a vignettes CSV (id, language, expected, messages)."""
    raw = (await file.read()).decode("utf-8-sig")
    tmp = Path(tempfile.gettempdir()) / "healthbot_vignette_upload.csv"
    tmp.write_text(raw, encoding="utf-8")
    try:
        return vignettes.import_csv(str(tmp))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid vignette CSV: {exc}")
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/vignettes/{vignette_id}/validate")
def validate_vignette(
    vignette_id: str,
    level: str = Form(...),
    validated_by: str = Form(...),
    notes: str = Form(""),
) -> dict:
    try:
        result = vignettes.validate(vignette_id, level, validated_by, notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown vignette {vignette_id}")
    return result


@app.get("/api/vignettes/export.csv")
def export_vignettes(include_pending: bool = False) -> Response:
    """Clinician-validated vignettes, ready for scripts.evaluate."""
    out = Path(tempfile.gettempdir()) / "healthbot_vignettes_validated.csv"
    count = vignettes.export_validated(str(out), include_pending=include_pending)
    body = out.read_text(encoding="utf-8")
    out.unlink(missing_ok=True)
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="vignettes_validated.csv"',
            "X-Vignette-Count": str(count),
        },
    )


@app.get("/api/export/triage.csv")
def export_triage_csv() -> Response:
    """Anonymised triage records as CSV, for offline analysis."""
    rows = records.export_rows()
    columns = [
        "session_id",
        "created_at",
        "channel",
        "language",
        "triage_level",
        "symptom_category",
        "reason",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="healthbot_triage_{stamp}.csv"'
        },
    )


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


# Serve the built React dashboard (dashboard/ → npm run build).
_DASH_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if _DASH_DIST.is_dir():
    app.mount("/dashboard", StaticFiles(directory=_DASH_DIST, html=True), name="dashboard")
