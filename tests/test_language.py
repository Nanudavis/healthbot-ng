import pytest

from app import conversation, language
from app.language import detect_language

ALICE = "whatsapp:+2348011111111"


# ── Heuristic detection ─────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Abeg my pikin dey hot, e no gree chop", "pidgin"),
        ("wetin dey do me, I no sabi", "pidgin"),
        ("Yarona yana da zazzabi tun jiya", "hausa"),
        ("ba ya numfashi sosai, muna bukatar asibiti", "hausa"),
        ("Omo mi ni iba, ara re gbona pupo", "yoruba"),
        ("Ọmọ mi kò le mí dáadáa", "yoruba"),
    ],
)
def test_detects_marked_languages(text, expected):
    assert detect_language(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "My child has had a fever since yesterday",
        "I have a headache and feel tired",
        "",
    ],
)
def test_plain_english_has_no_markers(text):
    assert detect_language(text) is None


# ── Localised deterministic safety messages ─────────────────────

def test_hausa_red_flag_gets_hausa_emergency_reply(fake_llm):
    reply = conversation.handle_message(ALICE, "yarona ba ya numfashi")
    assert "GAGGAWA" in reply
    assert "ASIBITI" in reply
    assert fake_llm == []  # LLM never consulted


def test_yoruba_red_flag_gets_yoruba_emergency_reply(fake_llm):
    reply = conversation.handle_message(ALICE, "omo mi ti daku, ara re gbona")
    assert "PAJAWIRI" in reply


def test_english_red_flag_gets_english_emergency_reply(fake_llm):
    reply = conversation.handle_message(ALICE, "my son is having convulsions")
    assert "EMERGENCY — GO NOW" in reply
    assert "NEAREST hospital" in reply


def test_fallback_is_localised(monkeypatch):
    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    reply = conversation.handle_message(ALICE, "yarona yana da zazzabi")
    assert reply == conversation.FALLBACKS["hausa"]


def test_language_sticks_across_turns(monkeypatch, fake_llm):
    # Turn 1 establishes Hausa; turn 2 has no markers but the session
    # remembers, so a deterministic message still comes out in Hausa.
    conversation.handle_message(ALICE, "yarona yana da zazzabi")

    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    reply = conversation.handle_message(ALICE, "since yesterday")
    assert reply == conversation.FALLBACKS["hausa"]


# ── LLM-reported language wins ──────────────────────────────────

def test_llm_reported_language_is_stored(monkeypatch):
    def _fake(messages):
        return (
            '{"triage": "PENDING", "language": "yoruba", '
            '"reason": "r", "reply": "Kini o n se e?"}'
        )

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    conversation.handle_message(ALICE, "nothing obviously marked here")
    session_id = conversation.store.anonymise(ALICE)
    assert conversation.store.get_meta(session_id, "language") == "yoruba"


def test_invalid_llm_language_is_ignored(monkeypatch):
    def _fake(messages):
        return (
            '{"triage": "PENDING", "language": "french", '
            '"reason": "r", "reply": "Question?"}'
        )

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    conversation.handle_message(ALICE, "hello")
    session_id = conversation.store.anonymise(ALICE)
    assert conversation.store.get_meta(session_id, "language") is None


def test_reset_forgets_language(fake_llm):
    conversation.handle_message(ALICE, "abeg wetin I go do")
    session_id = conversation.store.anonymise(ALICE)
    assert conversation.store.get_meta(session_id, "language") == "pidgin"
    conversation.handle_message(ALICE, "reset")
    assert conversation.store.get_meta(session_id, "language") is None


# ── Igbo ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "nwa m ahụ ya na-ekpo ọkụ",
        "biko nwa m adịghị ike",
        "ọ na-arịa ọrịa kemgbe ụnyaahụ",
    ],
)
def test_detects_igbo(text):
    assert detect_language(text) == "igbo"


def test_igbo_red_flag_gets_igbo_emergency_reply(fake_llm):
    reply = conversation.handle_message(ALICE, "nwa m na-ama ọgbọ")
    assert "IHE MBERE" in reply
    assert "ỤLỌ ỌGWỤ" in reply
    assert fake_llm == []  # decided without the LLM


def test_igbo_breathing_negation_is_a_red_flag(fake_llm):
    """'Not breathing' must fire; 'breathing well' must not."""
    from app import triage

    assert triage.contains_red_flag("ọ naghị eku ume")
    assert not triage.contains_red_flag("ọ na-eku ume nke ọma")


def test_igbo_fallback_is_localised(monkeypatch):
    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    reply = conversation.handle_message(ALICE, "nwa m nwere ahụ ọkụ")
    assert reply == conversation.FALLBACKS["igbo"]


def test_all_five_languages_have_every_safety_string():
    from app import triage

    for lang in ("english", "pidgin", "hausa", "yoruba", "igbo"):
        assert lang in triage.EMERGENCY_OVERRIDE_REPLIES
        assert lang in conversation.FALLBACKS
        assert lang in conversation.LOCATION_HINTS
