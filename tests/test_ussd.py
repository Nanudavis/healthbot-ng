import pytest
from fastapi.testclient import TestClient

from app import config, db, ussd
from app.main import app
from app.triage import TriageLevel

client = TestClient(app)


@pytest.fixture(autouse=True)
def ussd_db(tmp_path, monkeypatch):
    """USSD flows now persist an anonymised language preference, so tests
    must be isolated from the dev database and from each other."""
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/ussd.db")
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


def dial(text: str) -> str:
    return ussd.handle_ussd("ATUid_1", "+2348012345678", text)


# ── Menu walk ───────────────────────────────────────────────────

def test_first_screen_is_language_menu():
    reply = dial("")
    assert reply.startswith("CON ")
    assert "1. English" in reply
    assert "4. Yoruba" in reply


def test_language_choice_leads_to_who_menu():
    reply = dial("2")  # Pidgin
    assert reply.startswith("CON ")
    assert "Who dey sick?" in reply


def test_full_flow_child_fever_goes_to_clinic():
    # Pidgin → my pikin → fever → no danger → 1-3 days
    reply = dial("2*2*1*2*2")
    assert reply.startswith("END ")
    assert "GO CLINIC" in reply
    assert "no be doctor" in reply


def test_danger_sign_ends_with_emergency_immediately():
    # Pidgin → me → fever → danger YES (no duration question)
    reply = dial("2*1*1*1")
    assert reply.startswith("END ")
    assert "EMERGENCY" in reply


def test_not_sure_about_danger_escalates_to_emergency():
    reply = dial("1*1*2*3")  # English → me → cough → not sure
    assert reply.startswith("END ")
    assert "EMERGENCY" in reply


def test_adult_body_pain_today_is_self_care():
    reply = dial("1*1*4*2*1")  # English → me → body pain → no → today
    assert reply.startswith("END ")
    assert "SELF-CARE" in reply


def test_hausa_flow_ends_in_hausa():
    reply = dial("3*2*1*1")  # Hausa → child → fever → danger
    assert "GAGGAWA" in reply
    assert "ASIBITI" in reply


def test_yoruba_flow_ends_in_yoruba():
    reply = dial("4*1*1*1")
    assert "PAJAWIRI" in reply


# ── Invalid input handling ──────────────────────────────────────

def test_invalid_language_reshows_language_menu():
    reply = dial("9")
    assert reply.startswith("CON ")
    assert "Choose your language" in reply


def test_invalid_mid_flow_reshows_current_menu():
    reply = dial("1*7")  # 7 is not a valid "who" choice
    assert reply.startswith("CON ")
    assert "Who is sick?" in reply


def test_recovery_after_invalid_input():
    # Invalid "who" is ignored; the next entry fills it and flow continues.
    reply = dial("1*7*2")
    assert reply.startswith("CON ")
    assert "Main problem?" in reply


def test_repeated_invalid_inputs_end_the_session():
    """Every wasted screen costs the user (live USSD bills per session),
    so a stuck loop ends instead of looping forever."""
    reply = dial("9*9*9")
    assert reply.startswith("END ")
    assert "invalid" in reply.lower()


def test_invalid_streak_resets_on_valid_input():
    reply = dial("9*9*2")  # two bad language taps, then Pidgin
    assert reply.startswith("CON ")
    assert "Who dey sick?" in reply


# ── Back navigation ─────────────────────────────────────────────

def test_back_from_language_returns_to_language_menu():
    reply = dial("2*0")
    assert reply.startswith("CON ")
    assert "Choose your language" in reply


def test_back_pops_only_the_last_choice():
    reply = dial("2*1*0")  # Pidgin → me → back
    assert reply.startswith("CON ")
    assert "Who dey sick?" in reply


def test_back_then_forward_keeps_working():
    reply = dial("2*1*0*2")  # wrong who, back, choose child
    assert reply.startswith("CON ")
    assert "Wetin dey worry?" in reply  # symptom menu


# ── Saved language preference ───────────────────────────────────

def test_language_choice_is_remembered_across_sessions():
    dial("2")  # Pidgin — saved
    reply = dial("")  # new session
    assert reply.startswith("CON ")
    assert "Who dey sick?" in reply  # language screen skipped


def test_zero_after_saved_language_returns_to_language_menu():
    dial("2")
    reply = dial("0")  # back out of the who screen
    assert "Choose your language" in reply


def test_explicit_language_overrides_saved_preference():
    dial("2")  # saved Pidgin
    reply = dial("3")  # explicit Hausa on the next session
    assert "Wanene ba shi da lafiya?" in reply  # who menu in Hausa


# ── Rule table ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("who", "symptom", "danger", "duration", "expected"),
    [
        ("1", "1", "1", "1", TriageLevel.EMERGENCY),   # danger sign
        ("1", "4", "3", "1", TriageLevel.EMERGENCY),   # unsure = up
        ("1", "1", "2", "1", TriageLevel.CLINIC),      # any fever → test
        ("1", "5", "2", "1", TriageLevel.CLINIC),      # injury
        ("2", "2", "2", "1", TriageLevel.CLINIC),      # child cough
        ("2", "3", "2", "1", TriageLevel.CLINIC),      # child diarrhoea
        ("1", "2", "2", "3", TriageLevel.CLINIC),      # long duration
        ("1", "2", "2", "1", TriageLevel.SELF_CARE),   # adult cough, new
        ("1", "4", "2", "2", TriageLevel.SELF_CARE),   # adult pain, short
        ("2", "4", "2", "1", TriageLevel.SELF_CARE),   # child pain, today
    ],
)
def test_rule_table(who, symptom, danger, duration, expected):
    assert ussd.decide(who, symptom, danger, duration) == expected


def test_rule_table_never_returns_pending():
    for who in "123":
        for symptom in "123456":
            for danger in "123":
                for duration in "123":
                    level = ussd.decide(who, symptom, danger, duration)
                    assert level in (
                        TriageLevel.SELF_CARE,
                        TriageLevel.CLINIC,
                        TriageLevel.EMERGENCY,
                    )


# ── Screen constraints ──────────────────────────────────────────

def test_every_screen_fits_ussd_limit():
    screens = [f"CON {ussd.LANGUAGE_MENU}"]
    for lang in ussd.SCREENS.values():
        for key, text in lang.items():
            prefix = "END " if key in ("emergency", "clinic", "self") else "CON "
            screens.append(prefix + text)
    for screen in screens:
        assert len(screen) <= ussd.MAX_SCREEN_CHARS, screen


# ── Webhook ─────────────────────────────────────────────────────

def test_ussd_webhook_returns_plain_text():
    r = client.post(
        "/webhook/ussd",
        data={
            "sessionId": "ATUid_1",
            "serviceCode": "*347*88#",
            "phoneNumber": "+2348012345678",
            "text": "",
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("CON ")


def test_ussd_webhook_full_session():
    r = client.post(
        "/webhook/ussd",
        data={
            "sessionId": "ATUid_2",
            "serviceCode": "*347*88#",
            "phoneNumber": "+2348012345678",
            "text": "2*2*1*2*2",
        },
    )
    assert r.text.startswith("END ")
    assert "GO CLINIC" in r.text


# ── Igbo ────────────────────────────────────────────────────────

def test_igbo_is_option_five():
    reply = dial("")
    assert "5. Igbo" in reply
    assert dial("5").startswith("CON ")
    assert "Onye na-arịa?" in dial("5")


def test_igbo_full_flow_reaches_a_verdict():
    reply = dial("5*2*1*2*2")  # Igbo → child → fever → no danger → 1-3 days
    assert reply.startswith("END ")
    assert "GAA ỤLỌ ỌGWỤ" in reply


def test_igbo_danger_sign_ends_immediately():
    reply = dial("5*1*1*1")
    assert reply.startswith("END ")
    assert "IHE MBERE" in reply


def test_all_five_languages_have_every_screen():
    for lang in ("english", "pidgin", "hausa", "yoruba", "igbo"):
        assert lang in ussd.SCREENS
        assert set(ussd.SCREENS[lang]) == {
            "who", "symptom", "danger", "duration", "emergency", "clinic", "self",
        }
