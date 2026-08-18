import json

import pytest

from app import conversation, triage
from app.triage import TriageLevel

ALICE = "whatsapp:+2348011111111"


# ── JSON parsing ────────────────────────────────────────────────

def test_parse_valid_json():
    raw = json.dumps(
        {"triage": "SELF_CARE", "reason": "Mild headache", "reply": "Rest and drink water."}
    )
    result = triage.parse_triage_response(raw)
    assert result.level == TriageLevel.SELF_CARE
    assert result.reason == "Mild headache"
    assert result.reply == "Rest and drink water."


def test_parse_json_wrapped_in_code_fence():
    raw = '```json\n{"triage": "EMERGENCY", "reason": "r", "reply": "Go now"}\n```'
    result = triage.parse_triage_response(raw)
    assert result.level == TriageLevel.EMERGENCY


def test_parse_json_with_surrounding_prose():
    raw = 'Here is my answer: {"triage": "CLINIC", "reason": "r", "reply": "See a nurse"} hope that helps'
    result = triage.parse_triage_response(raw)
    assert result.level == TriageLevel.CLINIC


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '{"triage": "MAYBE_FINE", "reason": "r", "reply": "hmm"}',  # unknown level
        '{"reason": "r", "reply": "no triage key"}',
        '{"triage": "SELF_CARE", "reason": "r", "reply": ""}',  # empty reply
        "",
    ],
)
def test_broken_output_escalates_to_clinic(raw):
    result = triage.parse_triage_response(raw)
    assert result.level == TriageLevel.CLINIC
    assert "health worker" in result.reply


def test_lowercase_level_is_accepted():
    raw = '{"triage": "self_care", "reason": "r", "reply": "Rest well."}'
    assert triage.parse_triage_response(raw).level == TriageLevel.SELF_CARE


# ── Red-flag safety net ─────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "My son is having convulsions",                # English
        "She is unconscious and not breathing",        # English
        "im pikin dey shake body since morning",       # Pidgin
        "e don faint o",                               # Pidgin
        "yana da farfadiya",                           # Hausa
        "ba ya numfashi",                              # Hausa
        "o ti daku",                                   # Yoruba
        "irora aya to lagbara",                        # Yoruba
    ],
)
def test_red_flags_detected_across_languages(text):
    assert triage.contains_red_flag(text)


def test_ordinary_text_is_not_a_red_flag():
    assert not triage.contains_red_flag("I have a small headache since morning")
    assert not triage.contains_red_flag("my pikin get small catarrh")


def test_red_flag_bypasses_llm_entirely(fake_llm):
    reply = conversation.handle_message(ALICE, "My daughter is having convulsions")
    assert "🚨" in reply
    assert fake_llm == []  # the LLM was never called


def test_red_flag_works_even_when_llm_is_down(monkeypatch):
    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    reply = conversation.handle_message(ALICE, "e no dey breathe, e don faint")
    assert "🚨" in reply


# ── Reply formatting ────────────────────────────────────────────

def test_final_levels_get_banner():
    result = triage.TriageResult(TriageLevel.CLINIC, "r", "See a health worker today.")
    formatted = triage.format_reply(result)
    assert formatted.startswith("⚠️ VISIT CLINIC TODAY")
    assert "See a health worker today." in formatted


def test_pending_gets_no_banner():
    result = triage.TriageResult(TriageLevel.PENDING, "r", "How old is the child?")
    assert triage.format_reply(result) == "How old is the child?"


def test_emergency_verdict_from_llm_gets_banner(monkeypatch):
    def _fake(messages):
        return '{"triage": "EMERGENCY", "reason": "chest pain", "reply": "Go to the nearest hospital now."}'

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    reply = conversation.handle_message(ALICE, "something dey do me for body")
    assert reply.startswith("🚨 EMERGENCY — GO NOW")


# ── Expanded red-flag vocabulary ────────────────────────────────

@pytest.mark.parametrize(
    "phrase",
    [
        "face dey drop",
        "slurred speech",
        "one side weak",
        "throat dey close",
        "face dey swell",
        "pikin dey yellow",
        "not feeding",
        "won't feed",
        "fuska ta karkata",
        "oju wu",
        "ihu na-aza aza",
        "akpịrị na-afụ",
        # Prompt-tuning round: snake bite, newborn feeding, bleeding,
        # repeated vomiting, and head injury across languages
        "snake bit",
        "will not breastfeed",
        "keeps vomiting",
        "vomiting nonstop",
        "blood dey comot",
        "ba ya shan nono",
        "ya yi shiru",
        "zafi sosai",
        "maciji",
        "amai ba tsayawa",
        "ejo bu",
        "ko jeun",
        "agwọ",
    ],
)
def test_expanded_red_flag_vocabulary(phrase):
    """Stroke, anaphylaxis and newborn danger signs must be caught by the
    deterministic net, not left to the LLM."""
    assert triage.contains_red_flag(phrase)


@pytest.mark.parametrize(
    ("phrase", "expected_sign"),
    [
        ("ejo bu", "snake bite / poisoning"),
        ("ba ya shan nono", "newborn not feeding"),
        ("amai ba tsayawa", "repeated vomiting / possible head injury"),
        ("ya yi shiru", "newborn lethargy / weakness"),
    ],
)
def test_new_red_flags_map_to_clinical_signs(phrase, expected_sign):
    matched = triage.matched_red_flag(phrase)
    assert matched
    assert expected_sign in triage._sign_for(matched)


# ── Deterministic downgrade guard ───────────────────────────────

def test_guard_forces_emergency_when_danger_sign_in_history():
    level, reason, changed = triage.guard_verdict(
        TriageLevel.SELF_CARE,
        "my pikin don dey shake body since morning",
        3,
    )
    assert level == TriageLevel.EMERGENCY
    assert changed
    assert "danger sign" in reason


def test_guard_refuses_self_care_for_high_risk_presentation():
    level, _, changed = triage.guard_verdict(
        TriageLevel.SELF_CARE,
        "my wife dey pregnant and she get headache",
        3,
    )
    assert level == TriageLevel.CLINIC
    assert changed


def test_guard_refuses_self_care_before_two_user_turns():
    level, _, changed = triage.guard_verdict(TriageLevel.SELF_CARE, "small headache", 1)
    assert level == TriageLevel.CLINIC
    assert changed


def test_guard_allows_self_care_for_safe_multi_turn_case():
    level, _, changed = triage.guard_verdict(
        TriageLevel.SELF_CARE,
        "small headache since morning, I don rest, e dey better small",
        3,
    )
    assert level == TriageLevel.SELF_CARE
    assert not changed


def test_guard_leaves_clinic_and_emergency_untouched():
    level, _, changed = triage.guard_verdict(TriageLevel.CLINIC, "headache for two days", 3)
    assert level == TriageLevel.CLINIC and not changed
    level, _, changed = triage.guard_verdict(TriageLevel.EMERGENCY, "headache for two days", 3)
    assert level == TriageLevel.EMERGENCY and not changed
