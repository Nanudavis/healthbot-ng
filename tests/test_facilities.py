import pytest
from fastapi.testclient import TestClient

from app import config, conversation, db, facilities, records
from app.main import app

ALICE = "whatsapp:+2348011111111"

# Test geography: a PHC right next to the user, a hospital ~11 km away.
CSV = """name,facility_type,state,lga,latitude,longitude
Testtown PHC,PHC,FCT,AMAC,9.0000,7.4000
Testtown General Hospital,GENERAL_HOSPITAL,FCT,AMAC,9.0000,7.5000
Faraway Teaching Hospital,TEACHING_HOSPITAL,Lagos,Mushin,6.5170,3.3530
"""


@pytest.fixture
def facility_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    db.reset_engine()
    csv_path = tmp_path / "facilities.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    facilities.seed_facilities(str(csv_path))
    yield
    db.reset_engine()


def test_haversine_known_distance():
    # 0.1° of longitude at ~9°N ≈ 11 km
    d = facilities.haversine_km(9.0, 7.4, 9.0, 7.5)
    assert 10.5 < d < 11.5


def test_seed_replaces_table(facility_db, tmp_path):
    count = facilities.seed_facilities(str(tmp_path / "facilities.csv"))
    assert count == 3  # re-seeding does not duplicate


def test_nearest_non_emergency_is_the_phc(facility_db):
    facility, km = facilities.find_nearest(9.0010, 7.4010, emergency=False)
    assert facility.name == "Testtown PHC"
    assert km < 1


def test_emergency_prefers_hospital_over_closer_phc(facility_db):
    facility, km = facilities.find_nearest(9.0010, 7.4010, emergency=True)
    assert facility.facility_type == "GENERAL_HOSPITAL"
    assert facility.name == "Testtown General Hospital"


def test_empty_database_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/empty.db")
    db.reset_engine()
    db.init_db()
    assert facilities.find_nearest(9.0, 7.4) is None
    db.reset_engine()


def test_broken_database_returns_none(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////nonexistent/nope/x.db")
    db.reset_engine()
    assert facilities.find_nearest(9.0, 7.4) is None
    db.reset_engine()


# ── Conversation flow ───────────────────────────────────────────

def test_clinic_verdict_asks_for_location(monkeypatch):
    def _fake(messages):
        return '{"triage": "CLINIC", "language": "pidgin", "reason": "r", "reply": "Go clinic today."}'

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    reply = conversation.handle_message(ALICE, "body dey pain me")
    assert "📍" in reply


def test_pending_does_not_ask_for_location(fake_llm):
    reply = conversation.handle_message(ALICE, "I get small headache")
    assert "📍" not in reply


def test_location_after_emergency_routes_to_hospital(facility_db, fake_llm):
    conversation.handle_message(ALICE, "my pikin dey shake body")  # red flag
    reply = conversation.handle_message(ALICE, "", latitude=9.0010, longitude=7.4010)
    assert "Testtown General Hospital" in reply
    assert "🏥" in reply
    assert "km from you" in reply


def test_location_without_prior_triage_routes_to_nearest(facility_db):
    reply = conversation.handle_message(ALICE, "", latitude=9.0010, longitude=7.4010)
    assert "Testtown PHC" in reply


def test_location_with_no_facilities_degrades_safely(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/empty2.db")
    db.reset_engine()
    db.init_db()
    reply = conversation.handle_message(ALICE, "", latitude=9.0, longitude=7.4)
    assert "nearest clinic" in reply.lower() or "ile-iwosan" in reply.lower()
    # The gap is recorded (counts only — never coordinates), so coverage
    # problems are visible to the health authority.
    assert records.routing_misses()["total"] == 1
    db.reset_engine()


def test_webhook_location_fields(facility_db):
    client = TestClient(app)
    r = client.post(
        "/webhook/whatsapp",
        data={
            "From": ALICE,
            "Body": "",
            "Latitude": "9.0010",
            "Longitude": "7.4010",
        },
    )
    assert r.status_code == 200
    assert "Testtown PHC" in r.text


def test_reply_includes_a_maps_link(facility_db):
    facility, km = facilities.find_nearest(9.0010, 7.4010)
    reply = facilities.format_facility_reply(facility, km, "pidgin")
    assert "https://www.google.com/maps/dir/?api=1&destination=" in reply
    assert f"{facility.latitude},{facility.longitude}" in reply


def test_maps_link_uses_coordinates_not_the_name(facility_db):
    """Many Nigerian facilities are not searchable by name, and a wrong
    search result during an emergency is worse than none."""
    facility, _ = facilities.find_nearest(9.0010, 7.4010)
    link = facilities.maps_link(facility)
    assert "9.0" in link and "7.4" in link
    assert "Testtown" not in link


def test_maps_link_offers_directions_not_just_a_pin(facility_db):
    facility, _ = facilities.find_nearest(9.0010, 7.4010)
    # /dir/ navigates from wherever the person is — what someone told to
    # go now actually needs.
    assert "/maps/dir/" in facilities.maps_link(facility)


def test_igbo_facility_reply(facility_db):
    facility, km = facilities.find_nearest(9.0010, 7.4010)
    reply = facilities.format_facility_reply(facility, km, "igbo")
    assert "Ụlọ ọgwụ kacha nso" in reply
    assert "Ụzọ ị ga-esi gaa" in reply


def test_every_language_has_facility_strings():
    for lang in ("english", "pidgin", "hausa", "yoruba", "igbo"):
        assert lang in facilities.NEAREST_LEADS
        assert lang in facilities.NO_FACILITY_REPLIES
        assert lang in facilities.DIRECTIONS_LABELS
