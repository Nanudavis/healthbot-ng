from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Facility(Base):
    """Public health facility — public data, no PII concerns."""

    __tablename__ = "facilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # PHC | GENERAL_HOSPITAL | TEACHING_HOSPITAL
    facility_type: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(60), index=True)
    lga: Mapped[str] = mapped_column(String(80))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)


class AppSetting(Base):
    """Runtime settings changed from the admin console.

    Stored in the database rather than .env because a cloud deployment's
    filesystem is ephemeral — a redeploy would otherwise silently revert
    the model or key an administrator had set.
    """

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class SusResponse(Base):
    """One participant's System Usability Scale questionnaire.

    No PII: participants are identified by a study code (P01, P02…).
    Raw item scores are kept alongside the computed score so the
    per-item analysis in the report can be reproduced from the data.
    """

    __tablename__ = "sus_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_code: Mapped[str] = mapped_column(String(40), index=True)
    language: Mapped[str] = mapped_column(String(16), index=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)  # whatsapp | ussd
    answers: Mapped[str] = mapped_column(String(60))  # "4,2,5,1,4,2,5,1,4,2"
    score: Mapped[float] = mapped_column(Float)
    comments: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class ClinicalVignette(Base):
    """An evaluation vignette and its AI-drafted label.

    Clinicians' verdicts live in VignetteValidation — one row per
    clinician — so the study can report inter-rater reliability rather
    than resting on a single unchecked opinion.
    """

    __tablename__ = "clinical_vignettes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vignette_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    language: Mapped[str] = mapped_column(String(16), index=True)
    messages: Mapped[str] = mapped_column(Text)  # "||"-separated turns
    proposed_level: Mapped[str] = mapped_column(String(16))


class VignetteValidation(Base):
    """One clinician's verdict on one vignette.

    Multiple rows per vignette are the point: two independent raters
    let the study compute Cohen's kappa, and single-rater validation is
    a recognised weakness in vignette studies.
    """

    __tablename__ = "vignette_validations"
    __table_args__ = (UniqueConstraint("vignette_id", "validator", name="uq_vignette_validator"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vignette_id: Mapped[str] = mapped_column(String(40), index=True)
    validator: Mapped[str] = mapped_column(String(120), index=True)
    level: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ConversationTurn(Base):
    """One exchange, kept for clinical audit — only when
    STORE_TRANSCRIPTS is enabled.

    Off by default because these are the patient's own words. Text is
    scrubbed of phone numbers, emails and long digit strings before it
    is written, but scrubbing is a mitigation, not a guarantee, so the
    default remains not storing it at all.
    """

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(16))
    user_text: Mapped[str] = mapped_column(Text, default="")
    bot_text: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class FacilityReferral(Base):
    """One routing event: which facility a patient was pointed to.

    Anonymised by construction — the patient's coordinates are used to
    pick a facility and then discarded. Only the facility (public data)
    and its state/LGA are stored, so the dashboard can show referral
    pressure by area without holding anyone's location.
    """

    __tablename__ = "facility_referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility_id: Mapped[int] = mapped_column(Integer, index=True)
    facility_name: Mapped[str] = mapped_column(String(200))
    facility_type: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(60), index=True)
    lga: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    distance_km: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class RoutingMiss(Base):
    """One referral that could not be routed to any facility.

    The patient's coordinates are deliberately not stored — the no-PII
    promise is that coordinates pick a facility and are then discarded.
    The row therefore carries only the triage level and channel, which
    is enough for a health authority to see coverage gaps.
    """

    __tablename__ = "routing_misses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True)  # CLINIC | EMERGENCY
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class ChannelPreference(Base):
    """Anonymised per-phone preference for deterministic channels.

    phone_hash is the same one-way SHA-256 used for sessions — no PII.
    A returning USSD user can skip the language screen because the bot
    remembers the language they chose last time. Read/writes are
    fail-safe: a broken preference store must never break a flow.
    """

    __tablename__ = "channel_preferences"
    __table_args__ = (
        UniqueConstraint("phone_hash", "channel", name="uq_phone_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="ussd")
    language: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class SessionMessage(Base):
    """One conversation message in the opt-in database session store.

    Only used when SESSION_STORE=db. This persists transient user text,
    so it is a documented privacy tradeoff (like enabling transcripts):
    rows are TTL-purged on access and the session key remains the
    anonymised SHA-256 hash.
    """

    __tablename__ = "session_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class SessionMeta(Base):
    """Per-session metadata (language, last triage) for the DB store."""

    __tablename__ = "session_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class AiEvent(Base):
    """One LLM API call, for cost/latency observability.

    Anonymised by construction: model, provider, duration, tokens,
    estimated cost and outcome. Never stores prompt or reply text.
    """

    __tablename__ = "ai_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(64), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    ok: Mapped[bool] = mapped_column(Integer, default=1)
    error_type: Mapped[str | None] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class OutboundMessage(Base):
    """One queued WhatsApp reply for the async outbound worker.

    Idempotent per Twilio MessageSid so a webhook retry cannot enqueue a
    duplicate; retries use exponential backoff up to a configured cap.
    Body is the system-generated reply (never raw patient text).
    """

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_sid: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    to_number: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), index=True)  # pending|retrying|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    provider_message_id: Mapped[str | None] = mapped_column(String(64))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class TriageRecord(Base):
    """One final triage outcome. Anonymised by construction: session_id
    is a SHA-256 hash, and `reason` is a short clinical sentence the
    system generates (never raw user text)."""

    __tablename__ = "triage_records"
    __table_args__ = (
        # Composite index serving the alert/threshold queries (created_at
        # window + level grouping). Created by create_all on fresh
        # databases; migration 001 backfills pre-existing ones.
        Index("ix_triage_records_created_at_level", "created_at", "level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)  # whatsapp | ussd
    language: Mapped[str] = mapped_column(String(16), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)  # SELF_CARE | CLINIC | EMERGENCY
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
