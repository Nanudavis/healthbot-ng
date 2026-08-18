from fastapi.testclient import TestClient

from app.main import DISCLAIMER, app

client = TestClient(app)


def post_whatsapp(body: str, sender: str = "whatsapp:+2348012345678"):
    return client.post("/webhook/whatsapp", data={"Body": body, "From": sender})


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_reply_is_twiml_xml(fake_llm):
    r = post_whatsapp("My pikin dey hot")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<Response>" in r.text
    assert "How old is the child?" in r.text


def test_every_reply_carries_disclaimer(fake_llm):
    r = post_whatsapp("Abeg my pikin dey hot")
    assert DISCLAIMER in r.text


def test_empty_body_gets_welcome():
    r = post_whatsapp("")
    assert r.status_code == 200
    assert "Welcome to HealthBot NG" in r.text
    assert DISCLAIMER in r.text


def test_llm_failure_degrades_safely(monkeypatch):
    from app import conversation

    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    r = post_whatsapp("I get headache")
    assert r.status_code == 200
    assert "clinic" in r.text
    assert DISCLAIMER in r.text


def test_duplicate_message_sid_is_not_reprocessed(monkeypatch, fake_llm):
    """Twilio retries timed-out webhooks; the retry must replay the stored
    response, not run the triage pipeline a second time."""
    from app import conversation as conv

    calls = []
    original = conv.handle_message

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(conv, "handle_message", counting)
    data = {"Body": "hello", "From": "whatsapp:+2348012345678", "MessageSid": "SM123"}
    r1 = client.post("/webhook/whatsapp", data=data)
    r2 = client.post("/webhook/whatsapp", data=data)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.text == r2.text
    assert len(calls) == 1


def test_distinct_message_sids_are_both_processed(monkeypatch, fake_llm):
    from app import conversation as conv

    calls = []
    original = conv.handle_message
    monkeypatch.setattr(
        conv,
        "handle_message",
        lambda *a, **k: (calls.append(a), original(*a, **k))[1],
    )
    client.post(
        "/webhook/whatsapp",
        data={"Body": "hi", "From": "whatsapp:+2348012345678", "MessageSid": "SM1"},
    )
    client.post(
        "/webhook/whatsapp",
        data={"Body": "hi", "From": "whatsapp:+2348012345678", "MessageSid": "SM2"},
    )
    assert len(calls) == 2
