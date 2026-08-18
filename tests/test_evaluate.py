import pytest

from app import conversation
from scripts import evaluate


def rows(*triples):
    return [
        {"id": str(i), "language": lang, "expected": exp, "predicted": pred}
        for i, (lang, exp, pred) in enumerate(triples)
    ]


def test_score_accuracy_and_sensitivity():
    m = evaluate.score(
        rows(
            ("english", "EMERGENCY", "EMERGENCY"),
            ("english", "EMERGENCY", "CLINIC"),      # missed emergency
            ("pidgin", "CLINIC", "CLINIC"),
            ("pidgin", "SELF_CARE", "CLINIC"),       # over-triage
            ("hausa", "SELF_CARE", "SELF_CARE"),
            ("yoruba", "CLINIC", "PENDING"),         # no decision = wrong
        )
    )
    assert m["total"] == 6
    assert m["correct"] == 3
    assert m["accuracy"] == 0.5
    assert m["emergency_total"] == 2
    assert m["emergency_detected"] == 1
    assert m["emergency_sensitivity"] == 0.5
    assert m["over_triage"] == 1
    assert m["under_triage"] == 1  # the missed emergency
    assert m["by_language"]["english"] == {"correct": 1, "total": 2}


def test_score_confusion_matrix():
    m = evaluate.score(rows(("english", "CLINIC", "EMERGENCY")))
    assert m["confusion"]["CLINIC"]["EMERGENCY"] == 1
    assert m["confusion"]["CLINIC"]["CLINIC"] == 0


def test_score_no_emergencies_gives_null_sensitivity():
    m = evaluate.score(rows(("english", "SELF_CARE", "SELF_CARE")))
    assert m["emergency_sensitivity"] is None


def test_load_vignettes_parses_multi_turn(tmp_path):
    p = tmp_path / "v.csv"
    p.write_text(
        "id,language,expected,messages\n"
        'v1,pidgin,CLINIC,first message||second answer||third answer\n',
        encoding="utf-8",
    )
    vignettes = evaluate.load_vignettes(str(p))
    assert vignettes[0]["messages"] == ["first message", "second answer", "third answer"]


def test_load_vignettes_rejects_bad_level(tmp_path):
    p = tmp_path / "v.csv"
    p.write_text("id,language,expected,messages\nv1,english,MAYBE,hello\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        evaluate.load_vignettes(str(p))


def test_sample_vignette_file_is_valid():
    vignettes = evaluate.load_vignettes("eval/vignettes.sample.csv")
    assert len(vignettes) == 12
    assert {v["language"] for v in vignettes} == {"english", "pidgin", "hausa", "yoruba"}
    assert {v["expected"] for v in vignettes} == {"SELF_CARE", "CLINIC", "EMERGENCY"}


def test_full_vignette_set_is_valid_and_balanced():
    vignettes = evaluate.load_vignettes("eval/vignettes.csv")
    assert len(vignettes) >= 50
    by_lang = {}
    for v in vignettes:
        by_lang[v["language"]] = by_lang.get(v["language"], 0) + 1
    assert set(by_lang) == {"english", "pidgin", "hausa", "yoruba", "igbo"}
    assert min(by_lang.values()) >= 6  # every language represented
    emergencies = sum(1 for v in vignettes if v["expected"] == "EMERGENCY")
    assert emergencies >= 15  # enough for a meaningful sensitivity figure
    # Every level appears in every language, so per-language accuracy is
    # comparable rather than reflecting a different case mix.
    for lang in by_lang:
        levels = {v["expected"] for v in vignettes if v["language"] == lang}
        assert levels == {"SELF_CARE", "CLINIC", "EMERGENCY"}, lang


def test_no_non_emergency_vignette_trips_the_red_flag_net():
    """A CLINIC/SELF_CARE vignette containing a red-flag keyword would be
    force-escalated before the LLM ever runs — a guaranteed miss."""
    from app import triage

    for v in evaluate.load_vignettes("eval/vignettes.csv"):
        if v["expected"] != "EMERGENCY":
            for message in v["messages"]:
                assert not triage.contains_red_flag(message), (v["id"], message)


def test_run_vignette_stops_at_first_verdict(monkeypatch):
    calls = []

    def _fake(messages):
        calls.append(messages)
        if len(calls) == 1:
            return '{"triage": "PENDING", "reason": "r", "reply": "How old?"}'
        return '{"triage": "CLINIC", "reason": "r", "reply": "Go clinic."}'

    monkeypatch.setattr(conversation, "_chat_completion", _fake)
    predicted = evaluate.run_vignette(
        {"id": "t1", "language": "english", "messages": ["fever since yesterday", "3 years old", "unused"]}
    )
    assert predicted == "CLINIC"
    assert len(calls) == 2  # third scripted message never sent


def test_run_vignette_red_flag_needs_no_llm(monkeypatch):
    def _boom(messages):
        raise RuntimeError("no API key")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    predicted = evaluate.run_vignette(
        {"id": "t2", "language": "pidgin", "messages": ["my pikin dey shake body"]}
    )
    assert predicted == "EMERGENCY"


def test_run_vignette_nudges_then_reports_pending(monkeypatch):
    def _always_pending(messages):
        return '{"triage": "PENDING", "reason": "r", "reply": "One more question?"}'

    monkeypatch.setattr(conversation, "_chat_completion", _always_pending)
    predicted = evaluate.run_vignette(
        {"id": "t3", "language": "english", "messages": ["vague complaint"]}
    )
    assert predicted == "PENDING"


def test_run_vignette_api_error_reports_error(monkeypatch):
    def _boom(messages):
        raise RuntimeError("API down")

    monkeypatch.setattr(conversation, "_chat_completion", _boom)
    predicted = evaluate.run_vignette(
        {"id": "t4", "language": "english", "messages": ["ordinary complaint"]}
    )
    assert predicted == "ERROR"


def test_report_renders_all_tables():
    m = evaluate.score(
        rows(
            ("english", "EMERGENCY", "EMERGENCY"),
            ("pidgin", "CLINIC", "CLINIC"),
            ("hausa", "SELF_CARE", "SELF_CARE"),
        )
    )
    report = evaluate.render_report(m)
    assert "Table 5.1" in report
    assert "Table 5.2" in report
    assert "Table 5.3" in report
    assert "100.0%" in report
    assert "✅" in report
