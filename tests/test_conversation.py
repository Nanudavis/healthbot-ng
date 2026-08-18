from app import conversation
from app.sessions import SessionStore

ALICE = "whatsapp:+2348011111111"
BOB = "whatsapp:+2348022222222"


def test_phone_number_is_anonymised():
    session_id = SessionStore.anonymise(ALICE)
    assert ALICE not in session_id
    assert "+234" not in session_id
    assert session_id == SessionStore.anonymise(ALICE)  # stable


def test_multi_turn_memory(fake_llm):
    conversation.handle_message(ALICE, "My pikin dey hot")
    conversation.handle_message(ALICE, "E never reach 2 years")

    # Second LLM call must include the full first exchange.
    second_prompt = fake_llm[1]
    contents = [m["content"] for m in second_prompt]
    assert "My pikin dey hot" in contents
    assert "How old is the child?" in contents
    assert "E never reach 2 years" in contents
    assert second_prompt[0]["role"] == "system"


def test_sessions_are_isolated_per_number(fake_llm):
    conversation.handle_message(ALICE, "My pikin dey hot")
    conversation.handle_message(BOB, "I get small headache")

    bob_prompt = fake_llm[1]
    contents = [m["content"] for m in bob_prompt]
    assert "My pikin dey hot" not in contents
    assert "I get small headache" in contents


def test_reset_clears_history(fake_llm):
    conversation.handle_message(ALICE, "My pikin dey hot")
    reply = conversation.handle_message(ALICE, "reset")
    assert reply == conversation.WELCOME

    conversation.handle_message(ALICE, "New matter entirely")
    contents = [m["content"] for m in fake_llm[-1]]
    assert "My pikin dey hot" not in contents


def test_history_is_capped():
    store = SessionStore(max_messages=4)
    for i in range(10):
        store.append("sid", "user", f"msg {i}")
    history = store.history("sid")
    assert len(history) == 4
    assert history[-1]["content"] == "msg 9"


def test_failed_llm_call_does_not_pollute_history(monkeypatch, fake_llm):
    conversation.handle_message(ALICE, "My pikin dey hot")

    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    reply = conversation.handle_message(ALICE, "E dey vomit")
    assert reply == conversation.FALLBACK

    # The user's message stays (they said it), but no assistant turn was added.
    session_id = conversation.store.anonymise(ALICE)
    roles = [m["role"] for m in conversation.store.history(session_id)]
    assert roles == ["user", "assistant", "user"]


def test_prompt_forbids_asking_for_instrument_readings():
    """Field finding: the model asked for a thermometer reading, which
    most Nigerian households do not have. The prompt must steer it to
    observable signs instead."""
    prompt = conversation.SYSTEM_PROMPT.lower()
    assert "no medical equipment" in prompt
    assert "thermometer" in prompt
    assert "hot to touch" in prompt


def test_guard_escalates_high_risk_self_care_to_clinic(monkeypatch):
    from app import records

    calls = []

    def _fake(messages):
        calls.append(messages)
        if len(calls) == 1:
            return '{"triage":"PENDING","reason":"r","reply":"Any fever or vomiting?"}'
        return '{"triage":"SELF_CARE","language":"english","reason":"Seems mild","reply":"Rest at home."}'

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    conversation.handle_message(ALICE, "I get headache since morning")
    reply = conversation.handle_message(ALICE, "No fever, but my chest dey feel tight")

    # The model said SELF_CARE, but the conversation contains a high-risk
    # signal ("chest") — the guard must escalate and replace the
    # reassuring reply so the banner and the text do not contradict.
    assert "health worker" in reply
    assert records.summary()["by_level"] == {"CLINIC": 1}


def test_guard_refuses_first_turn_self_care(monkeypatch):
    from app import records

    def _fake(messages):
        return '{"triage":"SELF_CARE","language":"english","reason":"mild","reply":"Rest at home."}'

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    reply = conversation.handle_message(ALICE, "small headache")
    assert "health worker" in reply
    assert records.summary()["by_level"] == {"CLINIC": 1}
