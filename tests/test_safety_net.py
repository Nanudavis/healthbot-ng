"""Safety-netting: the signs that mean come back immediately.

Triage is a snapshot. A child correctly sent home can deteriorate hours
later, and the emergency-sensitivity figure — measured at the moment of
triage — never sees it. These return signs are the clinical control for
that, so they must be present on every non-emergency verdict rather
than left to whether the model mentioned them.
"""

import pytest

from app import conversation, safety_net, ussd
from app.triage import TriageLevel

LANGUAGES = ("english", "pidgin", "hausa", "yoruba", "igbo")


# ── The strings themselves ──────────────────────────────────────

@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_has_both_audiences(language):
    assert safety_net.return_signs(language, is_child=True)
    assert safety_net.return_signs(language, is_child=False)
    assert language in safety_net.RETURN_LEADS


@pytest.mark.parametrize("language", LANGUAGES)
def test_child_and_adult_signs_differ(language):
    """A young child's danger signs are not an adult's — IMCI is age
    banded, and generic advice would be clinically wrong for both."""
    assert safety_net.return_signs(language, True) != safety_net.return_signs(language, False)


def test_child_signs_cover_the_imci_danger_signs():
    signs = safety_net.return_signs("english", is_child=True).lower()
    assert "drink" in signs or "feed" in signs  # unable to drink/breastfeed
    assert "breathing" in signs                  # fast/difficult breathing
    assert "fits" in signs or "convuls" in signs # convulsions


def test_adult_signs_cover_adult_red_flags():
    signs = safety_net.return_signs("english", is_child=False).lower()
    assert "chest pain" in signs
    assert "breathing" in signs
    assert "wake" in signs


def test_unknown_language_falls_back_rather_than_failing():
    """A missing translation must never mean no safety advice."""
    assert safety_net.return_signs("swahili", True) == safety_net.return_signs("english", True)


# ── USSD ────────────────────────────────────────────────────────

def dial(text: str) -> str:
    return ussd.handle_ussd("s", "+2348011111111", text)


def test_clinic_verdict_names_specific_signs():
    reply = dial("1*2*1*2*2")  # English → child → fever → no danger → 1-3 days
    assert "GO NOW IF:" in reply
    assert "cannot drink" in reply
    # The old vague wording must be gone.
    assert "If it gets worse, go now" not in reply


def test_self_care_verdict_names_specific_signs():
    reply = dial("1*1*4*2*1")  # English → adult → body pain → no danger → today
    assert "SELF-CARE" in reply
    assert "GO NOW IF:" in reply
    assert "chest pain" in reply


def test_child_flow_gets_child_signs():
    child = dial("1*2*1*2*2")
    adult = dial("1*1*1*2*2")
    assert "cannot drink or feed" in child
    assert "chest pain" in adult
    assert "cannot drink or feed" not in adult


def test_emergency_gets_no_return_advice():
    """They are being told to go now; return advice would dilute it."""
    reply = dial("1*1*1*1")
    assert "EMERGENCY" in reply
    assert "GO NOW IF:" not in reply


def test_disclaimer_is_last_on_every_verdict():
    for code in ("1*2*1*2*2", "1*1*4*2*1", "1*1*1*1"):
        assert dial(code).rstrip().endswith("Guidance only - not a doctor.")


@pytest.mark.parametrize("lang_code", ["1", "2", "3", "4", "5"])
def test_every_language_carries_advice_and_disclaimer(lang_code):
    reply = dial(f"{lang_code}*2*1*2*2")
    lang = ussd.LANGS[lang_code]
    assert safety_net.RETURN_LEADS[lang] in reply
    assert ussd.DISCLAIMERS[lang] in reply


def test_all_verdict_screens_still_fit_the_ussd_limit():
    """Return advice must not push any screen past the gateway limit —
    a truncated safety message is worse than a terse one."""
    longest = 0
    for lang_code in "12345":
        for who in "123":
            for symptom in "123456":
                for duration in "123":
                    reply = dial(f"{lang_code}*{who}*{symptom}*2*{duration}")
                    longest = max(longest, len(reply))
                    assert len(reply) <= ussd.MAX_SCREEN_CHARS, reply
    assert longest > 100, "sanity: advice really is being appended"


# ── WhatsApp ────────────────────────────────────────────────────

@pytest.fixture
def clinic_reply(monkeypatch):
    def install(level: str, language: str, text: str):
        monkeypatch.setattr(
            conversation,
            "_chat_completion",
            lambda m: (
                f'{{"triage":"{level}","language":"{language}",'
                f'"reason":"r","reply":"{text}"}}'
            ),
        )

    return install


def test_whatsapp_clinic_carries_return_signs(clinic_reply):
    clinic_reply("CLINIC", "pidgin", "Carry am go clinic today.")
    reply = conversation.handle_message("whatsapp:+2348011111111", "my pikin dey hot")
    assert "GO NOW IF:" in reply
    assert "e no fit drink" in reply  # child signs, from "pikin"


def test_whatsapp_self_care_carries_return_signs(clinic_reply):
    clinic_reply("SELF_CARE", "english", "Rest and drink water.")
    reply = conversation.handle_message("whatsapp:+2348022222222", "I have a slight headache")
    assert "GO NOW IF:" in reply
    assert "chest pain" in reply  # adult signs


def test_whatsapp_emergency_has_no_return_advice(fake_llm):
    reply = conversation.handle_message("whatsapp:+2348033333333", "my pikin dey shake body")
    assert "🚨" in reply
    assert "GO NOW IF:" not in reply


def test_child_is_inferred_across_languages(clinic_reply):
    for number, message, marker in [
        ("+2348001", "my pikin dey hot", "e no fit drink"),
        ("+2348002", "yarona yana da zazzabi", "ba ya sha"),
        ("+2348003", "omo mi ni iba", "ko le mu omi"),
        ("+2348004", "nwa m nwere ahụ ọkụ", "ọ naghị aṅụ"),
    ]:
        lang = {"+2348001": "pidgin", "+2348002": "hausa",
                "+2348003": "yoruba", "+2348004": "igbo"}[number]
        clinic_reply("CLINIC", lang, "See a health worker today.")
        conversation.store.clear()
        reply = conversation.handle_message(f"whatsapp:{number}", message)
        assert marker in reply, (number, reply)


def test_advice_survives_a_model_that_never_mentions_it(clinic_reply):
    """The whole point: the safety floor cannot depend on the LLM."""
    clinic_reply("CLINIC", "english", "Go to the clinic.")
    reply = conversation.handle_message("whatsapp:+2348044444444", "my child has a fever")
    assert "GO NOW IF:" in reply
