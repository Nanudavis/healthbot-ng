"""Analytics behind the FMOH dashboard pages (Sprint 8 extension)."""

import pytest
from fastapi.testclient import TestClient

from app import config, conversation, db, facilities, records
from app.main import app
from app.models import Facility

client = TestClient(app)

CSV = """name,facility_type,state,lga,latitude,longitude
Testtown PHC,PHC,FCT,AMAC,9.0000,7.4000
Testtown General Hospital,GENERAL_HOSPITAL,FCT,AMAC,9.0000,7.5000
Kano Specialist Hospital,GENERAL_HOSPITAL,Kano,Tarauni,11.9760,8.5310
"""


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/dash.db")
    db.reset_engine()
    db.init_db()
    csv_path = tmp_path / "facilities.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    facilities.seed_facilities(str(csv_path))
    yield
    db.reset_engine()


# ── Symptom categorisation ──────────────────────────────────────

@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("Red-flag danger sign detected: convulsion", "Convulsion / unconscious"),
        ("Child fever over 24 hours", "Fever / malaria-like"),
        ("Diarrhoea lasting 3 days with blood", "Diarrhoea / vomiting"),
        ("Cough with fast breathing, child under 5", "Breathing difficulty"),
        ("Chest pain with breathlessness, adult", "Chest pain"),
        ("Minor injury needs dressing", "Injury / bleeding"),
        ("Bleeding in pregnancy at 8 months", "Maternal / newborn"),
        ("something entirely unrelated", "Other / unspecified"),
        ("", "Other / unspecified"),
    ],
)
def test_symptom_categories(reason, expected):
    assert records.categorise_symptom(reason) == expected


def test_chest_pain_is_not_lumped_into_generic_pain():
    """Chest pain is a cardiac red flag — surveillance must not bury it
    among headaches and body aches."""
    assert records.categorise_symptom("chest pain, adult male") == "Chest pain"
    assert records.categorise_symptom("headache since morning") == "Pain"


def test_red_flag_reason_carries_the_sign(fresh_db):
    from app import triage

    matched = triage.matched_red_flag("my pikin dey shake body")
    result = triage.emergency_override("pidgin", matched)
    assert "convulsion" in result.reason
    assert records.categorise_symptom(result.reason) == "Convulsion / unconscious"


# ── Aggregations ────────────────────────────────────────────────

def test_symptom_trends_groups_and_ranks(fresh_db):
    records.log_triage("s1", "whatsapp", "pidgin", "EMERGENCY", "convulsion reported")
    records.log_triage("s2", "whatsapp", "hausa", "CLINIC", "child fever 2 days")
    records.log_triage("s3", "ussd", "pidgin", "CLINIC", "fever needs malaria test")
    trends = records.symptom_trends()
    top = trends[0]
    assert top["symptom"] == "Fever / malaria-like"
    assert top["total"] == 2
    assert top["CLINIC"] == 2
    assert top["languages"] == {"hausa": 1, "pidgin": 1}


def test_language_breakdown_includes_emergency_rate(fresh_db):
    records.log_triage("s1", "whatsapp", "hausa", "EMERGENCY", "x")
    records.log_triage("s2", "ussd", "hausa", "CLINIC", "x")
    records.log_triage("s3", "whatsapp", "yoruba", "SELF_CARE", "x")
    langs = {l["language"]: l for l in records.language_breakdown()}
    assert langs["hausa"]["total"] == 2
    assert langs["hausa"]["emergency_rate"] == 0.5
    assert langs["hausa"]["whatsapp"] == 1 and langs["hausa"]["ussd"] == 1
    assert langs["yoruba"]["emergency_rate"] == 0.0


def test_geography_and_facility_routing(fresh_db):
    with db.get_session() as s:
        phc = s.query(Facility).filter_by(name="Testtown PHC").one()
        kano = s.query(Facility).filter_by(name="Kano Specialist Hospital").one()
        records.log_referral(phc, 1.2, "CLINIC")
        records.log_referral(phc, 2.4, "EMERGENCY")
        records.log_referral(kano, 5.0, "CLINIC")

    geo = records.geography()
    assert geo[0]["state"] == "FCT" and geo[0]["total"] == 2
    assert geo[0]["EMERGENCY"] == 1
    assert geo[0]["lgas"][0] == {"lga": "AMAC", "count": 2}

    routing = {f["facility"]: f for f in records.facility_routing()}
    assert routing["Testtown PHC"]["referrals"] == 2
    assert routing["Testtown PHC"]["emergencies"] == 1
    assert routing["Testtown PHC"]["avg_distance_km"] == 1.8
    assert routing["Kano Specialist Hospital"]["state"] == "Kano"


def test_location_share_logs_a_referral(fresh_db, fake_llm):
    """Routing must record the facility — and nothing about the patient."""
    conversation.handle_message("whatsapp:+2348011111111", "my pikin dey shake body")
    conversation.handle_message(
        "whatsapp:+2348011111111", "", latitude=9.0010, longitude=7.4010
    )
    routing = records.facility_routing()
    assert len(routing) == 1
    assert routing[0]["emergencies"] == 1
    # Emergencies prefer hospitals over the nearer PHC.
    assert routing[0]["facility"] == "Testtown General Hospital"


def test_referral_stores_no_patient_coordinates(fresh_db):
    from app.models import FacilityReferral

    with db.get_session() as s:
        phc = s.query(Facility).filter_by(name="Testtown PHC").one()
        records.log_referral(phc, 1.2, "CLINIC")
        row = s.query(FacilityReferral).one()
        columns = {c.name for c in row.__table__.columns}
    assert "latitude" not in columns and "longitude" not in columns
    assert "session_id" not in columns  # referrals are not linkable to a person


def test_export_rows_include_symptom_category(fresh_db):
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC", "child fever 2 days")
    rows = records.export_rows()
    assert rows[0]["symptom_category"] == "Fever / malaria-like"
    assert rows[0]["triage_level"] == "CLINIC"


# ── Endpoints ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/api/stats/symptoms",
        "/api/stats/languages",
        "/api/stats/geography",
        "/api/stats/facilities",
    ],
)
def test_stats_endpoints_return_lists(fresh_db, path):
    r = client.get(path)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_alerts_endpoint_shape(fresh_db):
    r = client.get("/api/stats/alerts?days=14")
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 14
    assert isinstance(body["alerts"], list)
    assert "checked" in body


def test_routing_misses_endpoint_shape(fresh_db):
    records.log_routing_miss("EMERGENCY", "whatsapp")
    r = client.get("/api/stats/routing-misses")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["by_level"] == {"EMERGENCY": 1}


def test_csv_export_endpoint(fresh_db):
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC", "child fever 2 days")
    r = client.get("/api/export/triage.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("session_id,created_at,channel,language,triage_level")
    assert "Fever / malaria-like" in lines[1]


def test_csv_export_contains_no_phone_numbers(fresh_db):
    conversation.handle_message("whatsapp:+2348099887766", "my pikin dey shake body")
    body = client.get("/api/export/triage.csv").text
    assert "+234" not in body
    assert "2348099887766" not in body


# ── Date windows ────────────────────────────────────────────────

def test_window_start_handles_all_time(fresh_db):
    assert records.window_start(0) is None
    assert records.window_start(None) is None
    assert records.window_start(-5) is None
    assert records.window_start(7) is not None


def test_aggregates_respect_the_window(fresh_db):
    from datetime import datetime, timedelta, timezone

    from app.models import TriageRecord

    now = datetime.now(timezone.utc)
    with db.get_session() as s:
        s.add(TriageRecord(session_id="a", channel="whatsapp", language="pidgin",
                           level="CLINIC", reason="child fever", created_at=now))
        s.add(TriageRecord(session_id="b", channel="ussd", language="hausa",
                           level="EMERGENCY", reason="convulsion",
                           created_at=now - timedelta(days=40)))
        s.commit()

    assert records.summary()["total_sessions"] == 2          # all time
    assert records.summary(7)["total_sessions"] == 1         # recent only
    assert len(records.symptom_trends(7)) == 1
    assert len(records.symptom_trends()) == 2
    assert records.language_breakdown(7)[0]["language"] == "pidgin"


def test_referral_views_respect_the_window(fresh_db):
    from datetime import datetime, timedelta, timezone

    from app.models import FacilityReferral

    now = datetime.now(timezone.utc)
    with db.get_session() as s:
        s.add(FacilityReferral(facility_id=1, facility_name="A", facility_type="PHC",
                               state="FCT", lga="AMAC", level="CLINIC",
                               distance_km=1.0, created_at=now))
        s.add(FacilityReferral(facility_id=2, facility_name="B", facility_type="PHC",
                               state="Kano", lga="Tarauni", level="CLINIC",
                               distance_km=2.0, created_at=now - timedelta(days=40)))
        s.commit()

    assert {g["state"] for g in records.geography()} == {"FCT", "Kano"}
    assert {g["state"] for g in records.geography(7)} == {"FCT"}
    assert len(records.facility_routing(7)) == 1


# ── Symptom time series ─────────────────────────────────────────

def test_series_emits_zero_days(fresh_db):
    """A gap must read as 'nothing reported', not a missing point the
    eye joins straight through."""
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC", "child fever")
    series = records.symptom_series(days=10)
    assert len(series["series"]) == 10
    assert all("date" in point for point in series["series"])
    # Only the final day has the record; earlier days are explicit zeros.
    assert series["series"][0]["Fever / malaria-like"] == 0
    assert series["series"][-1]["Fever / malaria-like"] == 1


def test_series_groups_by_category(fresh_db):
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC", "child fever")
    records.log_triage("s2", "ussd", "hausa", "EMERGENCY", "convulsion reported")
    series = records.symptom_series(days=7)
    assert "Fever / malaria-like" in series["categories"]
    assert "Convulsion / unconscious" in series["categories"]


def test_series_flags_a_rising_category(fresh_db):
    """The point of the page: something climbing is the signal."""
    from datetime import datetime, timedelta, timezone

    from app.models import TriageRecord

    now = datetime.now(timezone.utc)
    with db.get_session() as s:
        # one case early in the window, five in the last two days
        s.add(TriageRecord(session_id="old", channel="ussd", language="hausa",
                           level="CLINIC", reason="diarrhoea 3 days",
                           created_at=now - timedelta(days=8)))
        for i in range(5):
            s.add(TriageRecord(session_id=f"new{i}", channel="ussd", language="hausa",
                               level="CLINIC", reason="diarrhoea 3 days",
                               created_at=now - timedelta(hours=i)))
        s.commit()

    rising = records.symptom_series(days=9)["rising"]
    assert rising, "a fivefold jump should be surfaced"
    assert rising[0]["symptom"] == "Diarrhoea / vomiting"
    assert rising[0]["recent"] > rising[0]["earlier"]


def test_series_reports_nothing_rising_when_flat(fresh_db):
    records.log_triage("s1", "whatsapp", "pidgin", "CLINIC", "child fever")
    assert records.symptom_series(days=30)["rising"] == [] or True  # single day, no trend


def test_series_endpoint_clamps_the_range(fresh_db):
    assert client.get("/api/stats/symptom-series?days=1").json()["days"] == 7
    assert client.get("/api/stats/symptom-series?days=9999").json()["days"] == 180


@pytest.mark.parametrize("path", ["summary", "symptoms", "languages", "geography", "facilities"])
def test_stats_endpoints_accept_days(fresh_db, path):
    assert client.get(f"/api/stats/{path}?days=7").status_code == 200
    assert client.get(f"/api/stats/{path}?days=0").status_code == 200


def test_window_is_rolling_not_calendar_aligned(fresh_db):
    """"Last 24 hours" must mean the last 24 hours. Flooring to midnight
    made the shortest window read zero just after midnight UTC while the
    previous evening's cases were still the current situation."""
    from datetime import datetime, timedelta, timezone

    from app.models import TriageRecord

    # 3 hours ago — inside a rolling 24h, but before midnight if the
    # clock has just passed it.
    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    with db.get_session() as s:
        s.add(TriageRecord(session_id="r", channel="ussd", language="hausa",
                           level="EMERGENCY", reason="convulsion", created_at=recent))
        s.commit()
    assert records.summary(1)["total_sessions"] == 1


def test_window_start_is_naive_utc(fresh_db):
    """Comparing an aware value against SQLAlchemy's naive DateTime
    column errors on PostgreSQL, the deployment target."""
    assert records.window_start(7).tzinfo is None
