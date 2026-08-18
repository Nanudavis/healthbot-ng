import pytest
from fastapi.testclient import TestClient

from app import config, conversation, db, records, ussd
from app.main import app

client = TestClient(app)
ALICE = "whatsapp:+2348011111111"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/records.db")
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


def test_summary_math():
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC", "child fever")
    records.log_triage("s2", "ussd", "hausa", "EMERGENCY", "danger sign")
    records.log_triage("s3", "ussd", "english", "SELF_CARE", "mild pain")
    records.log_triage("s4", "whatsapp", "pidgin", "EMERGENCY", "convulsion")

    s = records.summary()
    assert s["total_sessions"] == 4
    assert s["emergencies"] == 2
    assert s["ussd_share"] == 0.5
    assert s["by_level"] == {"CLINIC": 1, "EMERGENCY": 2, "SELF_CARE": 1}
    assert s["by_language"]["pidgin"] == 2


def test_daily_has_full_window_with_zero_fill():
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC")
    days = records.daily(days=7)
    assert len(days) == 7
    assert days[-1]["CLINIC"] == 1  # today is the last bucket
    assert days[0] == {"date": days[0]["date"], "SELF_CARE": 0, "CLINIC": 0, "EMERGENCY": 0}


def test_recent_is_newest_first_and_limited():
    for i in range(5):
        records.log_triage(f"s{i}", "ussd", "yoruba", "CLINIC", f"case {i}")
    rows = records.recent(limit=3)
    assert len(rows) == 3
    assert rows[0]["reason"] == "case 4"
    assert all(r["minutes_ago"] == 0 for r in rows)


def test_whatsapp_final_verdict_writes_record(monkeypatch):
    def _fake(messages):
        return '{"triage": "CLINIC", "language": "pidgin", "reason": "Child fever over 24h", "reply": "Go clinic today."}'

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    conversation.handle_message(ALICE, "my pikin body dey hot small")

    s = records.summary()
    assert s["total_sessions"] == 1
    assert s["by_level"] == {"CLINIC": 1}
    assert s["by_channel"] == {"whatsapp": 1}


def test_whatsapp_pending_writes_nothing(fake_llm):
    conversation.handle_message(ALICE, "I get small headache")
    assert records.summary()["total_sessions"] == 0


def test_red_flag_override_writes_emergency_record(fake_llm):
    conversation.handle_message(ALICE, "my pikin dey shake body")
    s = records.summary()
    assert s["by_level"] == {"EMERGENCY": 1}


def test_ussd_end_writes_record_and_con_does_not():
    ussd.handle_ussd("ATU1", "+2348012345678", "2*2*1")  # mid-flow (CON)
    assert records.summary()["total_sessions"] == 0

    ussd.handle_ussd("ATU1", "+2348012345678", "2*2*1*2*2")  # END → clinic
    s = records.summary()
    assert s["total_sessions"] == 1
    assert s["by_channel"] == {"ussd": 1}
    rows = records.recent(1)
    assert rows[0]["reason"] == "child under 5, fever, no danger sign, 1-3 days"


def test_record_reason_contains_no_phone_number():
    ussd.handle_ussd("ATU1", "+2348012345678", "3*1*1*1")
    rows = records.recent(1)
    assert "+234" not in rows[0]["reason"]


def test_record_write_failure_does_not_break_reply(monkeypatch, fake_llm):
    monkeypatch.setattr(records.db, "get_session", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    reply = conversation.handle_message(ALICE, "my pikin dey shake body")
    assert "🚨" in reply  # emergency reply still delivered


# ── Routing coverage gaps ───────────────────────────────────────

def test_routing_miss_recorded_and_counted(isolated_db):
    records.log_routing_miss("EMERGENCY", "whatsapp")
    records.log_routing_miss("CLINIC", "whatsapp")
    records.log_routing_miss("EMERGENCY", "whatsapp")
    misses = records.routing_misses()
    assert misses["total"] == 3
    assert misses["by_level"] == {"EMERGENCY": 2, "CLINIC": 1}


def test_routing_miss_write_failure_is_fail_safe(isolated_db, monkeypatch):
    monkeypatch.setattr(records.db, "get_session", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    records.log_routing_miss("EMERGENCY")  # must not raise
    monkeypatch.undo()  # restore the real session before reading back
    assert records.routing_misses()["total"] == 0


# ── IDSR-style alerts ───────────────────────────────────────────

def _seed_triage(session, count, reason, offset_days):
    from datetime import datetime, timedelta, timezone

    from app.models import TriageRecord

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(count):
        session.add(
            TriageRecord(
                session_id=f"{offset_days}-{i}",
                channel="whatsapp",
                language="english",
                level="CLINIC",
                reason=reason,
                created_at=now - timedelta(days=offset_days),
            )
        )


def test_alert_triggers_on_doubling(isolated_db):
    with db.get_session() as s:
        _seed_triage(s, 20, "diarrhoea with dehydration", 21)  # previous window
        _seed_triage(s, 50, "diarrhoea with dehydration", 1)   # current window
        s.commit()

    result = records.alerts(14)
    by_label = {a["label"]: a for a in result["alerts"]}
    alert = by_label["Diarrhoea / vomiting"]
    assert alert["current"] == 50
    assert alert["previous"] == 20
    assert alert["ratio"] == 2.5
    assert "verification" in alert["message"]


def test_alert_requires_minimum_count(isolated_db):
    with db.get_session() as s:
        _seed_triage(s, 4, "diarrhoea", 21)
        _seed_triage(s, 6, "diarrhoea", 1)
        s.commit()

    # 6 vs 4 is only 1.5× — below the 2.0 multiplier, so no alert.
    assert records.alerts(14)["alerts"] == []

    with db.get_session() as s:
        _seed_triage(s, 3, "fever", 1)  # above 2× but below the minimum
        s.commit()
    assert all(a["label"] != "Fever / malaria-like" for a in records.alerts(14)["alerts"])


def test_alert_fires_as_new_signal_when_previous_window_empty(isolated_db):
    with db.get_session() as s:
        _seed_triage(s, 8, "fever and chills", 1)
        s.commit()

    by_label = {a["label"]: a for a in records.alerts(14)["alerts"]}
    alert = by_label["Fever / malaria-like"]
    assert alert["previous"] == 0
    assert alert["ratio"] is None
    assert "New signal" in alert["message"]


def test_alert_covers_emergency_referrals_by_state(isolated_db):
    from datetime import datetime, timedelta, timezone

    from app.models import FacilityReferral

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.get_session() as s:
        for i in range(3):
            s.add(
                FacilityReferral(
                    facility_id=1, facility_name="A", facility_type="GENERAL_HOSPITAL",
                    state="Kano", lga="Tarauni", level="EMERGENCY",
                    distance_km=2.0, created_at=now - timedelta(days=21),
                )
            )
        for i in range(8):
            s.add(
                FacilityReferral(
                    facility_id=1, facility_name="A", facility_type="GENERAL_HOSPITAL",
                    state="Kano", lga="Tarauni", level="EMERGENCY",
                    distance_km=2.0, created_at=now - timedelta(days=1),
                )
            )
        s.commit()

    labels = [a["label"] for a in records.alerts(14)["alerts"]]
    assert "Emergency referrals in Kano" in labels


# ── API endpoints ───────────────────────────────────────────────

def test_summary_endpoint():
    records.log_triage("s1", "ussd", "hausa", "EMERGENCY", "danger sign")
    r = client.get("/api/stats/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sessions"] == 1
    assert body["emergencies"] == 1


def test_daily_endpoint():
    r = client.get("/api/stats/daily?days=3")
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_recent_endpoint():
    records.log_triage("s1", "whatsapp", "yoruba", "SELF_CARE", "mild headache")
    r = client.get("/api/stats/recent")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["level"] == "SELF_CARE"
    assert set(body[0]) == {"level", "language", "channel", "reason", "minutes_ago"}


# ── Clinical audit transcripts (opt-in) ─────────────────────────

def test_transcripts_are_off_by_default(monkeypatch, fake_llm):
    """Conversations are the patient's own words — not stored unless an
    ethics approval covers it."""
    monkeypatch.setattr(config, "STORE_TRANSCRIPTS", False)
    conversation.handle_message("whatsapp:+2348011111111", "my pikin dey hot")
    sid = conversation.store.anonymise("whatsapp:+2348011111111")
    assert records.transcript(sid) == []


def test_transcripts_stored_when_enabled(monkeypatch, fake_llm):
    monkeypatch.setattr(config, "STORE_TRANSCRIPTS", True)
    conversation.handle_message("whatsapp:+2348011111111", "my pikin dey hot")
    sid = conversation.store.anonymise("whatsapp:+2348011111111")
    rows = records.transcript(sid)
    assert len(rows) == 1
    assert rows[0]["user"] == "my pikin dey hot"
    assert rows[0]["level"] == "PENDING"


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("call me on +234 803 123 4567", "803"),
        ("my email is ada@example.com", "@example.com"),
        ("my NIN is 12345678901", "12345678901"),
    ],
)
def test_scrubbing_removes_identifiers(raw, must_not_contain):
    assert must_not_contain not in records.scrub(raw)


def test_scrubbing_keeps_clinical_meaning():
    text = "my pikin dey hot since 2 days, e vomit 3 times"
    cleaned = records.scrub(text)
    assert "pikin" in cleaned and "hot" in cleaned
    assert "2 days" in cleaned  # short numbers are clinical, not identifying


def test_stored_turns_are_scrubbed(monkeypatch, fake_llm):
    monkeypatch.setattr(config, "STORE_TRANSCRIPTS", True)
    conversation.handle_message(
        "whatsapp:+2348011111111", "my pikin dey hot, call me on 08031234567"
    )
    sid = conversation.store.anonymise("whatsapp:+2348011111111")
    stored = records.transcript(sid)[0]["user"]
    assert "08031234567" not in stored
    assert "pikin" in stored


def test_audit_write_failure_never_breaks_the_reply(monkeypatch, fake_llm):
    monkeypatch.setattr(config, "STORE_TRANSCRIPTS", True)
    monkeypatch.setattr(db, "get_session", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    reply = conversation.handle_message("whatsapp:+2348099999999", "I get headache")
    assert reply  # patient still gets an answer
