"""Triage record persistence + research analytics (Sprint 8).

Writing a record must never break a patient reply — every write is
fail-safe. Reads power the FMOH dashboard endpoints.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from app import config, db
from app.models import (
    AiEvent,
    ChannelPreference,
    ConversationTurn,
    FacilityReferral,
    RoutingMiss,
    TriageRecord,
)

log = logging.getLogger(__name__)

# Symptom grouping for research analytics. Applied to the system-generated
# English `reason`, never to the patient's own words — those are not
# stored. Order matters: the first category that matches wins, so the
# more urgent/specific patterns are listed first.
SYMPTOM_PATTERNS = (
    ("Convulsion / unconscious", ("convuls", "seizure", "unconscious", "not wake", "faint", "coma")),
    # Obstetric and neonatal cases group together whatever the
    # accompanying symptom: bleeding in pregnancy is an obstetric
    # emergency, not a generic injury, and fever in a newborn is a
    # referral emergency rather than an ordinary fever.
    ("Maternal / newborn", ("pregnan", "labour", "newborn", "neonat", "infant")),
    ("Chest pain", ("chest pain", "ciwon kirji", "irora aya", "cardiac")),
    ("Breathing difficulty", ("breath", "cough", "chest indraw", "wheez", "pneumon")),
    ("Fever / malaria-like", ("fever", "hot", "malaria", "temperature", "chills")),
    ("Diarrhoea / vomiting", ("diarrh", "vomit", "stool", "purg", "dehydrat", "ors")),
    ("Injury / bleeding", ("injur", "bleed", "wound", "burn", "fracture", "bite", "cut")),
    ("Pain", ("pain", "ache", "headache")),
)
OTHER_SYMPTOM = "Other / unspecified"


def window_start(days: int | None) -> datetime | None:
    """Start of the reporting window, or None for all time.

    Aggregates default to all time, which is fine at a few hundred
    records and useless at fifty thousand — so every research-analytics view
    takes the same window and the dashboard passes one value to all.

    Rolling, not calendar-aligned: "last 24 hours" must mean the last
    24 hours. Flooring to midnight made the shortest window read zero
    shortly after midnight UTC while cases from the evening before were
    still the current situation. Charts that need day buckets align
    them themselves.

    Returned naive-UTC to match how SQLAlchemy stores DateTime columns;
    comparing an aware value against a naive column errors on
    PostgreSQL, which is the deployment target.
    """
    if not days or days <= 0:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def _within(stmt, column, days: int | None):
    since = window_start(days)
    return stmt if since is None else stmt.where(column >= since)


def categorise_symptom(reason: str) -> str:
    """Bucket a clinical reason line into a surveillance category."""
    text = (reason or "").lower()
    for label, patterns in SYMPTOM_PATTERNS:
        if any(p in text for p in patterns):
            return label
    return OTHER_SYMPTOM


def log_triage(
    session_id: str, channel: str, language: str, level: str, reason: str = ""
) -> None:
    try:
        db.init_db()
        with db.get_session() as session:
            session.add(
                TriageRecord(
                    session_id=session_id,
                    channel=channel,
                    language=language,
                    level=level,
                    reason=reason[:300],
                )
            )
            session.commit()
    except Exception:
        log.warning("Could not persist triage record", exc_info=True)


def summary(days: int | None = None) -> dict:
    with db.get_session() as session:
        total = session.scalar(
            _within(select(func.count(TriageRecord.id)), TriageRecord.created_at, days)
        ) or 0
        by_level = _group_counts(session, TriageRecord.level, days)
        by_channel = _group_counts(session, TriageRecord.channel, days)
        by_language = _group_counts(session, TriageRecord.language, days)
    return {
        "total_sessions": total,
        "emergencies": by_level.get("EMERGENCY", 0),
        "ussd_share": round(by_channel.get("ussd", 0) / total, 3) if total else 0.0,
        "by_level": by_level,
        "by_channel": by_channel,
        "by_language": by_language,
    }


def daily(days: int = 7) -> list[dict]:
    """Per-day counts by triage level for the last `days` days."""
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    with db.get_session() as session:
        rows = session.execute(
            select(
                func.date(TriageRecord.created_at),
                TriageRecord.level,
                func.count(TriageRecord.id),
            )
            .where(TriageRecord.created_at >= since)
            .group_by(func.date(TriageRecord.created_at), TriageRecord.level)
        ).all()

    by_date: dict[str, dict] = {}
    for offset in range(days):
        day = (since + timedelta(days=offset)).date().isoformat()
        by_date[day] = {"date": day, "SELF_CARE": 0, "CLINIC": 0, "EMERGENCY": 0}
    for date_str, level, count in rows:
        day = str(date_str)
        if day in by_date and level in by_date[day]:
            by_date[day][level] = count
    return list(by_date.values())


def recent(limit: int = 10) -> list[dict]:
    with db.get_session() as session:
        rows = session.scalars(
            select(TriageRecord).order_by(TriageRecord.created_at.desc()).limit(limit)
        ).all()
    now = datetime.now(timezone.utc)
    return [
        {
            "level": r.level,
            "language": r.language,
            "channel": r.channel,
            "reason": r.reason,
            "minutes_ago": max(0, int((now - _as_utc(r.created_at)).total_seconds() // 60)),
        }
        for r in rows
    ]


def log_referral(facility, distance_km: float, level: str) -> None:
    """Record a routing event. Never breaks the patient reply."""
    try:
        db.init_db()
        with db.get_session() as session:
            session.add(
                FacilityReferral(
                    facility_id=facility.id,
                    facility_name=facility.name,
                    facility_type=facility.facility_type,
                    state=facility.state,
                    lga=facility.lga,
                    level=level,
                    distance_km=round(distance_km, 2),
                )
            )
            session.commit()
    except Exception:
        log.warning("Could not persist facility referral", exc_info=True)


def log_routing_miss(level: str, channel: str = "whatsapp") -> None:
    """Record that a referral could not be routed. Never breaks the
    patient reply; stores nothing about where they were."""
    try:
        db.init_db()
        with db.get_session() as session:
            session.add(RoutingMiss(level=level, channel=channel))
            session.commit()
    except Exception:
        log.warning("Could not persist routing miss", exc_info=True)


def routing_misses(days: int | None = None) -> dict:
    """Coverage gaps: referrals that found no facility, by triage level."""
    db.init_db()  # a pre-existing database may predate this table
    with db.get_session() as session:
        rows = session.execute(
            _within(
                select(RoutingMiss.level, func.count(RoutingMiss.id)),
                RoutingMiss.created_at,
                days,
            ).group_by(RoutingMiss.level)
        ).all()
    by_level = dict(rows)
    return {"total": sum(by_level.values()), "by_level": by_level}


def save_channel_preference(phone_hash: str, channel: str, language: str) -> None:
    """Remember a per-phone language choice. Never breaks the flow."""
    try:
        db.init_db()
        with db.get_session() as session:
            row = session.scalar(
                select(ChannelPreference).where(
                    ChannelPreference.phone_hash == phone_hash,
                    ChannelPreference.channel == channel,
                )
            )
            if row:
                row.language = language
                row.updated_at = datetime.now(timezone.utc)
            else:
                session.add(
                    ChannelPreference(
                        phone_hash=phone_hash,
                        channel=channel,
                        language=language,
                    )
                )
            session.commit()
    except Exception:
        log.warning("Could not persist channel preference", exc_info=True)


def channel_preference(phone_hash: str, channel: str) -> str | None:
    """Last remembered language for a phone, or None."""
    try:
        db.init_db()
        with db.get_session() as session:
            row = session.scalar(
                select(ChannelPreference).where(
                    ChannelPreference.phone_hash == phone_hash,
                    ChannelPreference.channel == channel,
                )
            )
            return row.language if row else None
    except Exception:
        log.warning("Could not read channel preference", exc_info=True)
        return None


def log_ai_event(
    provider: str,
    model: str,
    duration_ms: int,
    ok: bool,
    attempt: int = 1,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
    error_type: str | None = None,
) -> None:
    """Record one LLM API call for observability. Never breaks the flow."""
    try:
        db.init_db()
        with db.get_session() as session:
            session.add(
                AiEvent(
                    provider=provider[:64],
                    model=model[:64],
                    duration_ms=int(duration_ms),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    estimated_cost_usd=estimated_cost_usd,
                    ok=ok,
                    error_type=(error_type or "")[:40] or None,
                    attempt=attempt,
                )
            )
            session.commit()
    except Exception:
        log.warning("Could not persist AI event", exc_info=True)


def ai_events(limit: int = 50) -> list[dict]:
    """Recent LLM calls for the observability console."""
    with db.get_session() as session:
        rows = session.scalars(
            select(AiEvent).order_by(AiEvent.created_at.desc(), AiEvent.id.desc()).limit(limit)
        ).all()
    return [
        {
            "provider": r.provider,
            "model": r.model,
            "duration_ms": r.duration_ms,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "estimated_cost_usd": r.estimated_cost_usd,
            "ok": bool(r.ok),
            "error_type": r.error_type,
            "attempt": r.attempt,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def alerts(days: int | None = None) -> dict:
    """IDSR-style threshold checks on a trailing window.

    The rule is deliberately deterministic and documented: for each
    series (symptom category from triage records, and emergency
    referrals per state), compare the last `days` against the previous
    `days`. Alert when the current count is at least ALERT_MIN_COUNT
    and at least ALERT_MULTIPLIER × the previous count. A series with
    no comparable previous reports is a new signal once the minimum is
    met. Nothing here is a confirmed outbreak — the UI labels these
    "community signal, requires verification".
    """
    days = max(7, min(days or config.ALERT_WINDOW_DAYS, 90))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_start = now - timedelta(days=days)
    previous_start = now - timedelta(days=2 * days)

    def split_counts(rows):
        current: dict[str, int] = {}
        previous: dict[str, int] = {}
        for created_at, label in rows:
            bucket = current if created_at >= current_start else previous
            bucket[label] = bucket.get(label, 0) + 1
        return current, previous

    with db.get_session() as session:
        triage_rows = session.execute(
            select(TriageRecord.created_at, TriageRecord.reason).where(
                TriageRecord.created_at >= previous_start
            )
        ).all()
        referral_rows = session.execute(
            select(FacilityReferral.created_at, FacilityReferral.state).where(
                FacilityReferral.created_at >= previous_start,
                FacilityReferral.level == "EMERGENCY",
            )
        ).all()

    symptom_current, symptom_previous = split_counts(
        (created_at, categorise_symptom(reason)) for created_at, reason in triage_rows
    )
    state_current, state_previous = split_counts(
        (created_at, f"Emergency referrals in {state}")
        for created_at, state in referral_rows
        if state
    )

    def check(series: dict[str, int], previous: dict[str, int]) -> list[dict]:
        found = []
        for label, curr in sorted(series.items(), key=lambda kv: kv[1], reverse=True):
            prev = previous.get(label, 0)
            if curr < config.ALERT_MIN_COUNT:
                continue
            if prev and curr < config.ALERT_MULTIPLIER * prev:
                continue
            ratio = round(curr / prev, 1) if prev else None
            if prev:
                message = (
                    f"{label}: {curr} in the last {days} days vs {prev} in the "
                    f"previous {days} ({ratio}×) — community signal, requires verification."
                )
            else:
                message = (
                    f"{label}: {curr} in the last {days} days — no comparable "
                    f"cases in the previous {days}. New signal, requires verification."
                )
            found.append(
                {
                    "label": label,
                    "current": curr,
                    "previous": prev,
                    "ratio": ratio,
                    "window_days": days,
                    "message": message,
                }
            )
        return found

    found = check(symptom_current, symptom_previous) + check(state_current, state_previous)
    found.sort(
        key=lambda a: a["current"] / max(a["previous"], 1),
        reverse=True,
    )
    return {
        "window_days": days,
        "alerts": found,
        "checked": len(symptom_current) + len(state_current),
    }


def symptom_trends(days: int | None = None) -> list[dict]:
    """Symptom categories, each split by triage level. Highest first."""
    with db.get_session() as session:
        rows = session.execute(
            _within(
                select(TriageRecord.reason, TriageRecord.level, TriageRecord.language),
                TriageRecord.created_at,
                days,
            )
        ).all()

    buckets: dict[str, dict] = {}
    for reason, level, language in rows:
        label = categorise_symptom(reason)
        b = buckets.setdefault(
            label,
            {"symptom": label, "total": 0, "SELF_CARE": 0, "CLINIC": 0, "EMERGENCY": 0, "languages": {}},
        )
        b["total"] += 1
        if level in b:
            b[level] += 1
        b["languages"][language] = b["languages"].get(language, 0) + 1
    return sorted(buckets.values(), key=lambda b: b["total"], reverse=True)


def language_breakdown(days: int | None = None) -> list[dict]:
    """Per-language volume and triage mix — feeds the per-language
    comparison the evaluation chapter needs."""
    with db.get_session() as session:
        rows = session.execute(
            _within(
                select(TriageRecord.language, TriageRecord.level, TriageRecord.channel),
                TriageRecord.created_at,
                days,
            )
        ).all()

    langs: dict[str, dict] = {}
    for language, level, channel in rows:
        entry = langs.setdefault(
            language,
            {
                "language": language,
                "total": 0,
                "SELF_CARE": 0,
                "CLINIC": 0,
                "EMERGENCY": 0,
                "whatsapp": 0,
                "ussd": 0,
            },
        )
        entry["total"] += 1
        if level in entry:
            entry[level] += 1
        if channel in entry:
            entry[channel] += 1
    for entry in langs.values():
        entry["emergency_rate"] = (
            round(entry["EMERGENCY"] / entry["total"], 3) if entry["total"] else 0.0
        )
    return sorted(langs.values(), key=lambda e: e["total"], reverse=True)


def geography(days: int | None = None) -> list[dict]:
    """Referral pressure by state and LGA (from routed facilities —
    no patient location is ever stored)."""
    with db.get_session() as session:
        rows = session.execute(
            _within(
                select(
                    FacilityReferral.state,
                    FacilityReferral.lga,
                    FacilityReferral.level,
                    func.count(FacilityReferral.id),
                ),
                FacilityReferral.created_at,
                days,
            ).group_by(FacilityReferral.state, FacilityReferral.lga, FacilityReferral.level)
        ).all()

    states: dict[str, dict] = {}
    for state, lga, level, count in rows:
        s = states.setdefault(
            state, {"state": state, "total": 0, "EMERGENCY": 0, "lgas": {}}
        )
        s["total"] += count
        if level == "EMERGENCY":
            s["EMERGENCY"] += count
        s["lgas"][lga] = s["lgas"].get(lga, 0) + count
    result = []
    for s in states.values():
        s["lgas"] = sorted(
            ({"lga": k, "count": v} for k, v in s["lgas"].items()),
            key=lambda x: x["count"],
            reverse=True,
        )
        result.append(s)
    return sorted(result, key=lambda s: s["total"], reverse=True)


def facility_routing(days: int | None = None) -> list[dict]:
    """Which facilities are receiving referrals, and how far patients
    are travelling to reach them."""
    with db.get_session() as session:
        rows = session.execute(
            _within(
                select(
                    FacilityReferral.facility_name,
                    FacilityReferral.facility_type,
                    FacilityReferral.state,
                    FacilityReferral.lga,
                    func.count(FacilityReferral.id),
                    func.avg(FacilityReferral.distance_km),
                    func.sum(case((FacilityReferral.level == "EMERGENCY", 1), else_=0)),
                ),
                FacilityReferral.created_at,
                days,
            ).group_by(
                FacilityReferral.facility_name,
                FacilityReferral.facility_type,
                FacilityReferral.state,
                FacilityReferral.lga,
            )
        ).all()
    return sorted(
        (
            {
                "facility": name,
                "type": ftype,
                "state": state,
                "lga": lga,
                "referrals": count,
                "avg_distance_km": round(float(avg or 0), 1),
                "emergencies": int(emerg or 0),
            }
            for name, ftype, state, lga, count, avg, emerg in rows
        ),
        key=lambda f: f["referrals"],
        reverse=True,
    )


def export_rows() -> list[dict]:
    """Pseudonymised triage records for CSV export / offline analysis."""
    with db.get_session() as session:
        rows = session.scalars(
            select(TriageRecord).order_by(TriageRecord.created_at.desc())
        ).all()
    return [
        {
            "session_id": r.session_id,
            "created_at": _as_utc(r.created_at).isoformat(),
            "channel": r.channel,
            "language": r.language,
            "triage_level": r.level,
            "symptom_category": categorise_symptom(r.reason),
            "reason": r.reason,
        }
        for r in rows
    ]


def _group_counts(session, column, days: int | None = None) -> dict:
    stmt = _within(
        select(column, func.count(TriageRecord.id)), TriageRecord.created_at, days
    ).group_by(column)
    return {key: count for key, count in session.execute(stmt).all()}


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Clinical audit transcripts (opt-in) ─────────────────────────

# Patterns scrubbed before any conversation text is stored. People type
# phone numbers and addresses unprompted, and the no-PII rule holds even
# when audit is switched on.
_SCRUB = (
    (re.compile(r"\+?\d[\d\s\-()]{8,}\d"), "[number]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"\b\d{6,}\b"), "[digits]"),
)


def scrub(text: str) -> str:
    """Remove identifying patterns from free text."""
    cleaned = text or ""
    for pattern, replacement in _SCRUB:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned.strip()[:2000]


def log_turn(
    session_id: str, turn: int, language: str, user_text: str, bot_text: str, level: str
) -> None:
    """Store one exchange for clinical audit. No-op unless enabled."""
    if not config.STORE_TRANSCRIPTS:
        return
    try:
        db.init_db()
        with db.get_session() as session:
            session.add(
                ConversationTurn(
                    session_id=session_id,
                    turn=turn,
                    language=language,
                    user_text=scrub(user_text),
                    bot_text=scrub(bot_text),
                    level=level,
                )
            )
            session.commit()
    except Exception:
        log.warning("Could not persist conversation turn", exc_info=True)


def transcript(session_id: str) -> list[dict]:
    """Every stored turn for one session, oldest first."""
    with db.get_session() as session:
        rows = session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.session_id == session_id)
            .order_by(ConversationTurn.turn)
        ).all()
        return [
            {
                "turn": r.turn,
                "language": r.language,
                "user": r.user_text,
                "bot": r.bot_text,
                "level": r.level,
                "at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def symptom_series(days: int = 30) -> dict:
    """Per-day counts for each symptom category.

    This is what makes the page a surveillance tool rather than a
    summary: an epidemiologist needs to see diarrhoea climbing over
    three weeks, not just its total. Days with no cases are emitted as
    zero so a gap reads as "nothing reported" rather than a missing
    point the eye joins straight through.
    """
    # Day-aligned here, unlike the rolling window used for totals: the
    # chart plots one point per calendar day.
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )

    with db.get_session() as session:
        rows = session.execute(
            select(TriageRecord.created_at, TriageRecord.reason, TriageRecord.level)
            .where(TriageRecord.created_at >= since)
            .order_by(TriageRecord.created_at)
        ).all()

    counts: dict[str, dict[str, int]] = {}
    emergencies: dict[str, dict[str, int]] = {}
    for created_at, reason, level in rows:
        day = _as_utc(created_at).date().isoformat()
        label = categorise_symptom(reason)
        counts.setdefault(day, {})[label] = counts.setdefault(day, {}).get(label, 0) + 1
        if level == "EMERGENCY":
            emergencies.setdefault(day, {})[label] = (
                emergencies.setdefault(day, {}).get(label, 0) + 1
            )

    categories = sorted({c for day in counts.values() for c in day})
    series = []
    for offset in range(days):
        day = (since + timedelta(days=offset)).date().isoformat()
        point = {"date": day}
        for category in categories:
            point[category] = counts.get(day, {}).get(category, 0)
        series.append(point)

    # A category rising sharply is the signal worth surfacing; compare
    # the most recent third of the window against the earliest third.
    third = max(1, days // 3)
    movers = []
    for category in categories:
        early = sum(p[category] for p in series[:third])
        late = sum(p[category] for p in series[-third:])
        if late > early and late >= 3:
            movers.append(
                {
                    "symptom": category,
                    "earlier": early,
                    "recent": late,
                    "change": round((late - early) / early, 2) if early else None,
                }
            )
    movers.sort(key=lambda m: m["recent"] - m["earlier"], reverse=True)

    return {
        "days": days,
        "categories": categories,
        "series": series,
        "rising": movers[:3],
        "emergency_by_day": [
            {"date": p["date"], "count": sum(emergencies.get(p["date"], {}).values())}
            for p in series
        ],
    }
