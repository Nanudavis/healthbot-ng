"""Compatibility with OpenAI-compatible third-party gateways, which may
serve Claude/GPT/other models with differing parameter support."""

import pytest

from app import config, conversation, rag


@pytest.fixture(autouse=True)
def clean_unsupported():
    conversation._unsupported.clear()
    yield
    conversation._unsupported.clear()


class FakeCompletions:
    """Rejects the named parameters the way a real provider would."""

    def __init__(self, rejects=(), reply='{"triage":"PENDING","reason":"r","reply":"ok"}'):
        self.rejects = set(rejects)
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        for param in self.rejects:
            if param in kwargs:
                raise Exception(f"Unsupported parameter: '{param}' is not supported by this model")

        class Msg:
            content = self.reply

        class Choice:
            message = Msg()

        class Response:
            choices = [Choice()]

        return Response()


@pytest.fixture
def fake_client(monkeypatch):
    def install(rejects=(), reply=None):
        completions = FakeCompletions(
            rejects,
            reply if reply is not None else '{"triage":"PENDING","reason":"r","reply":"ok"}',
        )

        class Client:
            chat = type("Chat", (), {"completions": completions})()

        monkeypatch.setattr(conversation, "_client", lambda: Client())
        return completions

    return install


def test_base_url_passed_to_client(monkeypatch):
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(conversation, "OpenAI", fake_openai)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://gateway.example/v1")
    conversation._client()
    assert captured["base_url"] == "https://gateway.example/v1"
    assert captured["api_key"] == "test-key"


def test_blank_base_url_means_openai_direct(monkeypatch):
    captured = {}
    monkeypatch.setattr(conversation, "OpenAI", lambda **kw: captured.update(kw) or object())
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
    conversation._client()
    assert captured["base_url"] is None


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        conversation._client()


def test_model_rejecting_json_mode_still_works(fake_client):
    """Claude models via gateways typically refuse response_format."""
    completions = fake_client(rejects=["response_format"])
    out = conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "PENDING" in out
    assert len(completions.calls) == 2  # first attempt, then retry
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_model_rejecting_temperature_still_works(fake_client):
    completions = fake_client(rejects=["temperature"])
    conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "temperature" not in completions.calls[-1]


def test_max_tokens_renamed_to_max_completion_tokens(fake_client):
    completions = fake_client(rejects=["max_tokens"])
    conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "max_tokens" not in completions.calls[-1]
    assert completions.calls[-1]["max_completion_tokens"] == 400


def test_both_token_param_names_rejected_sends_neither(fake_client):
    """Some OpenAI-style models reject max_tokens; GPT-style models reject
    max_completion_tokens; a provider rejecting both must still work and
    simply use its own default token limit."""
    completions = fake_client(rejects=["max_tokens", "max_completion_tokens"])
    out = conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "PENDING" in out
    final = completions.calls[-1]
    assert "max_tokens" not in final
    assert "max_completion_tokens" not in final


def test_multiple_rejected_params_all_dropped(fake_client):
    completions = fake_client(rejects=["response_format", "temperature"])
    conversation._chat_completion([{"role": "user", "content": "hi"}])
    final = completions.calls[-1]
    assert "response_format" not in final and "temperature" not in final
    assert final["messages"] == [{"role": "user", "content": "hi"}]


def test_unsupported_params_remembered_across_calls(fake_client):
    completions = fake_client(rejects=["response_format"])
    conversation._chat_completion([{"role": "user", "content": "one"}])
    calls_after_first = len(completions.calls)
    conversation._chat_completion([{"role": "user", "content": "two"}])
    # Second message costs a single call — no repeated probing.
    assert len(completions.calls) == calls_after_first + 1


def test_extra_params_are_sent(fake_client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_EXTRA_PARAMS", {"mode": "sol"})
    completions = fake_client()
    conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert completions.calls[-1]["mode"] == "sol"


def test_rejected_extra_param_is_dropped(fake_client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_EXTRA_PARAMS", {"mode": "sol"})
    completions = fake_client(rejects=["mode"])
    out = conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "PENDING" in out
    assert "mode" not in completions.calls[-1]


def test_malformed_extra_params_are_ignored(monkeypatch):
    monkeypatch.setenv("OPENAI_EXTRA_PARAMS", "not json at all")
    assert config._extra_params() == {}
    monkeypatch.setenv("OPENAI_EXTRA_PARAMS", '["a", "list"]')
    assert config._extra_params() == {}
    monkeypatch.setenv("OPENAI_EXTRA_PARAMS", '{"reasoning_effort":"high"}')
    assert config._extra_params() == {"reasoning_effort": "high"}


def test_genuine_errors_are_not_swallowed(monkeypatch):
    """Rate limits, auth failures etc. must propagate, not trigger the
    parameter-stripping retry loop."""

    class Failing:
        def create(self, **kwargs):
            raise RuntimeError("rate limit exceeded")

    class Client:
        chat = type("Chat", (), {"completions": Failing()})()

    monkeypatch.setattr(conversation, "_client", lambda: Client())
    with pytest.raises(RuntimeError, match="rate limit"):
        conversation._chat_completion([{"role": "user", "content": "hi"}])


def test_empty_model_output_is_retried_then_fails(monkeypatch, fake_client):
    """A provider can return an empty completion (observed with DeepSeek
    JSON mode). It must be retried like a transient error, and after the
    retries are exhausted it must raise so the caller sends the
    language-safe fallback - not let an empty string reach the parser."""
    monkeypatch.setattr(conversation.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 1)
    fake_client(reply="")
    with pytest.raises(RuntimeError, match="Empty model output"):
        conversation._chat_completion([{"role": "user", "content": "hi"}])


def test_empty_model_output_recovers_on_retry(monkeypatch):
    calls = {"n": 0}

    class Flaky:
        def create(self, **kwargs):
            calls["n"] += 1
            content = (
                ""
                if calls["n"] == 1
                else '{"triage":"PENDING","reason":"r","reply":"ok"}'
            )
            msg_content = content

            class Msg:
                content = msg_content

            class Choice:
                message = Msg()

            class Response:
                choices = [Choice()]

            return Response()

    class Client:
        chat = type("Chat", (), {"completions": Flaky()})()

    monkeypatch.setattr(conversation, "_client", lambda: Client())
    monkeypatch.setattr(conversation.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 3)
    out = conversation._chat_completion([{"role": "user", "content": "hi"}])
    assert "PENDING" in out
    assert calls["n"] == 2


def test_embeddings_use_dedicated_provider_when_set(monkeypatch):
    captured = {}

    class FakeEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "chat-key")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "embed-key")
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setitem(
        __import__("sys").modules,
        "langchain_openai",
        type("M", (), {"OpenAIEmbeddings": FakeEmbeddings}),
    )
    rag._embeddings()
    assert captured["api_key"] == "embed-key"
    assert captured["base_url"] == "https://api.openai.com/v1"


def test_embeddings_fall_back_to_chat_provider(monkeypatch):
    captured = {}

    class FakeEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "chat-key")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "")
    monkeypatch.setitem(
        __import__("sys").modules,
        "langchain_openai",
        type("M", (), {"OpenAIEmbeddings": FakeEmbeddings}),
    )
    rag._embeddings()
    assert captured["api_key"] == "chat-key"
    assert captured["base_url"] == "https://gateway.example/v1"


def test_local_embedding_provider_needs_no_api_key(monkeypatch):
    captured = {}

    class FakeHF:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")  # no key at all
    monkeypatch.setattr(config, "LOCAL_EMBEDDING_MODEL", "some/model")
    monkeypatch.setitem(
        __import__("sys").modules,
        "langchain_huggingface",
        type("M", (), {"HuggingFaceEmbeddings": FakeHF}),
    )
    rag._embeddings()
    assert captured["model_name"] == "some/model"
    assert "api_key" not in captured
