"""Multi-model comparison harness (Table 5.4–5.6)."""

import json

import pytest

from app import config, conversation
from scripts import compare_models, evaluate

MODELS = [
    {"name": "Model A", "model": "model-a", "base_url": "", "api_key_env": "KEY_A"},
    {
        "name": "Model B",
        "model": "model-b",
        "base_url": "https://b.example/v1",
        "api_key_env": "KEY_B",
    },
]

VIGNETTES = [
    {"id": "v1", "language": "english", "expected": "EMERGENCY", "messages": ["chest pain and sweating"]},
    {"id": "v2", "language": "pidgin", "expected": "CLINIC", "messages": ["my pikin dey hot"]},
]


@pytest.fixture(autouse=True)
def restore_config(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "original")
    monkeypatch.setattr(config, "OPENAI_MODEL", "original-model")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://original/v1")
    monkeypatch.setattr(config, "OPENAI_EXTRA_PARAMS", {})
    conversation._unsupported.clear()
    yield
    conversation._unsupported.clear()


def test_load_models_validates(tmp_path):
    good = tmp_path / "m.json"
    good.write_text(json.dumps(MODELS), encoding="utf-8")
    assert len(compare_models.load_models(str(good))) == 2

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"name": "no model key"}]), encoding="utf-8")
    with pytest.raises(SystemExit):
        compare_models.load_models(str(bad))

    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        compare_models.load_models(str(empty))


def test_activate_points_engine_at_the_model(monkeypatch):
    monkeypatch.setenv("KEY_B", "secret-b")
    assert compare_models._activate(MODELS[1]) is None
    assert config.OPENAI_API_KEY == "secret-b"
    assert config.OPENAI_MODEL == "model-b"
    assert config.OPENAI_BASE_URL == "https://b.example/v1"


def test_activate_reports_missing_key(monkeypatch):
    monkeypatch.delenv("KEY_A", raising=False)
    problem = compare_models._activate(MODELS[0])
    assert "KEY_A" in problem
    # Config must be left alone when the model cannot run.
    assert config.OPENAI_MODEL == "original-model"


def test_activate_clears_learned_parameter_support(monkeypatch):
    """Models differ in which parameters they reject; carrying one
    model's findings into the next would skew the comparison."""
    monkeypatch.setenv("KEY_A", "k")
    conversation._unsupported.add("response_format")
    compare_models._activate(MODELS[0])
    assert conversation._unsupported == set()


def test_run_model_skips_when_key_missing(monkeypatch):
    monkeypatch.delenv("KEY_A", raising=False)
    result = compare_models.run_model(MODELS[0], VIGNETTES)
    assert result["skipped"] and result["rows"] == []


def test_run_model_collects_predictions_and_latency(monkeypatch):
    monkeypatch.setenv("KEY_A", "k")
    monkeypatch.setattr(
        conversation,
        "_chat_completion",
        lambda messages: '{"triage":"CLINIC","reason":"r","reply":"go clinic"}',
    )
    result = compare_models.run_model(MODELS[0], VIGNETTES)
    assert len(result["rows"]) == 2
    assert {r["model"] for r in result["rows"]} == {"Model A"}
    # v1 contains "chest pain" — the deterministic net decides it, no LLM.
    v1 = next(r for r in result["rows"] if r["id"] == "v1")
    assert v1["predicted"] == "EMERGENCY"
    v2 = next(r for r in result["rows"] if r["id"] == "v2")
    assert v2["predicted"] == "CLINIC"
    assert all(r["seconds"] >= 0 for r in result["rows"])
    assert all(r["turns"] >= 1 for r in result["rows"])


def test_summarise_adds_latency_and_errors():
    result = {
        "name": "Model A",
        "rows": [
            {"model": "A", "id": "1", "language": "english", "expected": "EMERGENCY",
             "predicted": "EMERGENCY", "seconds": 2.0, "turns": 1},
            {"model": "A", "id": "2", "language": "pidgin", "expected": "CLINIC",
             "predicted": "SELF_CARE", "seconds": 4.0, "turns": 2},
            {"model": "A", "id": "3", "language": "hausa", "expected": "CLINIC",
             "predicted": "ERROR", "seconds": 1.0, "turns": 1},
        ],
    }
    s = compare_models.summarise(result)
    assert s["name"] == "Model A"
    assert s["errors"] == 1
    assert s["under_triage"] == 1
    assert s["median_seconds"] == 3.0  # errors excluded from latency
    assert s["mean_turns"] == 1.3


def test_render_produces_all_three_tables():
    rows = [
        {"model": "A", "id": "1", "language": "english", "expected": "EMERGENCY", "predicted": "EMERGENCY"},
        {"model": "A", "id": "2", "language": "hausa", "expected": "CLINIC", "predicted": "CLINIC"},
    ]
    summary = evaluate.score(rows)
    summary.update({"name": "Model A", "median_seconds": 3.0, "mean_turns": 2.0, "errors": 0})
    report = compare_models.render(
        [summary], [{"name": "Model B", "skipped": "KEY_B not set in .env"}], 2
    )
    assert "Table 5.4" in report and "Table 5.5" in report and "Table 5.6" in report
    assert "Model A" in report
    assert "English" in report and "Hausa" in report
    assert "Model B" in report and "KEY_B" in report  # skips are disclosed
    assert "Under-triage is the unsafe direction" in report


def test_render_discloses_the_red_flag_caveat():
    """Readers must know some vignettes never reach the model, or they
    will over-interpret identical scores across models."""
    rows = [{"model": "A", "id": "1", "language": "english", "expected": "CLINIC", "predicted": "CLINIC"}]
    summary = evaluate.score(rows)
    summary.update({"name": "A", "median_seconds": 1.0, "mean_turns": 1.0, "errors": 0})
    report = compare_models.render([summary], [], 1)
    assert "red-flag safety net runs before any model call" in report


def test_models_json_shipped_with_repo_is_valid():
    models = compare_models.load_models("eval/models.json")
    assert len(models) >= 2
    assert all("api_key_env" in m for m in models)
