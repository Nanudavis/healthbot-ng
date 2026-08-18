"""System Usability Scale capture and scoring."""

import pytest
from fastapi.testclient import TestClient

from app import config, db, sus
from app.main import app

client = TestClient(app)

BEST = [5, 1, 5, 1, 5, 1, 5, 1, 5, 1]
WORST = [1, 5, 1, 5, 1, 5, 1, 5, 1, 5]
NEUTRAL = [3] * 10


@pytest.fixture
def sus_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/sus.db")
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


# ── Scoring (the part that must not be wrong) ───────────────────

def test_reference_scores():
    """Anchors from the instrument: best possible 100, worst 0,
    all-neutral 50. If these drift, every reported score is wrong."""
    assert sus.score_answers(BEST) == 100.0
    assert sus.score_answers(WORST) == 0.0
    assert sus.score_answers(NEUTRAL) == 50.0


def test_alternating_polarity_is_respected():
    """Even-numbered items are negatively worded; agreeing with them
    must lower the score, not raise it."""
    base = [3] * 10
    agree_positive = list(base)
    agree_positive[0] = 5  # item 1, positive wording
    assert sus.score_answers(agree_positive) > 50.0

    agree_negative = list(base)
    agree_negative[1] = 5  # item 2, negative wording
    assert sus.score_answers(agree_negative) < 50.0


def test_score_is_zero_to_hundred_for_all_inputs():
    import itertools
    import random

    for _ in range(200):
        answers = [random.randint(1, 5) for _ in range(10)]
        score = sus.score_answers(answers)
        assert 0.0 <= score <= 100.0


@pytest.mark.parametrize(
    "answers",
    [[5] * 9, [5] * 11, [0] + [3] * 9, [6] + [3] * 9, ["x"] + [3] * 9],
)
def test_invalid_answers_rejected(answers):
    with pytest.raises(ValueError):
        sus.score_answers(answers)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(85, "A (excellent)"), (70, "B (good)"), (55, "C (OK)"), (40, "D (poor)"), (20, "F (unacceptable)")],
)
def test_grades(score, expected):
    assert sus.grade(score) == expected


# ── Recording and aggregation ───────────────────────────────────

def test_record_and_summary(sus_db):
    sus.record("P01", BEST, "pidgin", "whatsapp")
    sus.record("P02", NEUTRAL, "hausa", "ussd", "e dey try but e slow small")
    s = sus.summary()
    assert s["n"] == 2
    assert s["mean"] == 75.0
    assert s["min"] == 50.0 and s["max"] == 100.0
    assert s["meets_target"] is True
    assert s["by_language"]["pidgin"] == {"n": 1, "mean": 100.0}
    assert s["by_channel"]["ussd"] == {"n": 1, "mean": 50.0}
    assert s["responses"][1]["comments"] == "e dey try but e slow small"


def test_summary_is_empty_safe(sus_db):
    s = sus.summary()
    assert s["n"] == 0 and s["mean"] is None and s["responses"] == []


def test_meets_target_is_strictly_above_68(sus_db):
    """68 is 'average' — the target is above it, not equal to it.

    Note a property of the instrument: individual SUS scores are always
    multiples of 2.5, so a single response can never equal 68 exactly.
    67.5 must fail the target and 70.0 must pass it.
    """
    below = [4, 2, 4, 2, 4, 2, 4, 2, 4, 5]
    assert sus.score_answers(below) == 67.5
    sus.record("P01", below)
    assert sus.summary()["meets_target"] is False

    above = [4, 2, 4, 2, 4, 2, 4, 2, 5, 1]
    assert sus.score_answers(above) > 68
    sus.record("P02", above)
    assert sus.summary()["meets_target"] is True


def test_item_means_flag_polarity(sus_db):
    sus.record("P01", BEST)
    items = sus.summary()["item_means"]
    assert len(items) == 10
    assert items[0]["positive"] is True and items[0]["mean"] == 5.0
    assert items[1]["positive"] is False and items[1]["mean"] == 1.0
    assert items[0]["text"] == sus.ITEMS[0]


def test_record_requires_participant_code(sus_db):
    with pytest.raises(ValueError, match="participant_code"):
        sus.record("  ", BEST)


def test_std_dev_needs_two_responses(sus_db):
    sus.record("P01", BEST)
    assert sus.summary()["std_dev"] == 0.0
    sus.record("P02", WORST)
    assert sus.summary()["std_dev"] > 0


# ── Endpoints ───────────────────────────────────────────────────

def test_survey_page_renders_all_ten_items():
    r = client.get("/survey")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for item in sus.ITEMS:
        assert item in r.text
    assert "__ITEMS__" not in r.text  # placeholder was substituted


def test_survey_collects_no_personal_data():
    """The form must have no field capable of collecting identity —
    participants are identified only by a study code."""
    import re

    html = client.get("/survey").text
    field_names = set(re.findall(r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', html))
    # Exactly these four, so any identity field added later fails here.
    assert field_names == {"code", "language", "channel", "comments"}
    assert not field_names & {"phone", "name", "email", "address", "age", "dob"}


def test_submit_and_summary_endpoints(sus_db):
    r = client.post(
        "/api/sus",
        data={
            "participant_code": "P07",
            "answers": ",".join(str(a) for a in BEST),
            "language": "yoruba",
            "channel": "whatsapp",
            "comments": "very easy",
        },
    )
    assert r.status_code == 200
    assert r.json()["score"] == 100.0
    assert r.json()["grade"].startswith("A")

    s = client.get("/api/sus/summary").json()
    assert s["n"] == 1 and s["mean"] == 100.0


@pytest.mark.parametrize(
    "answers", ["1,2,3", "1,2,3,4,5,6,7,8,9,99", "not,numbers,at,all"]
)
def test_submit_rejects_bad_answers(sus_db, answers):
    r = client.post(
        "/api/sus", data={"participant_code": "P01", "answers": answers}
    )
    assert r.status_code == 400


def test_sus_csv_export(sus_db):
    sus.record("P01", BEST, "pidgin", "whatsapp", "good")
    r = client.get("/api/export/sus.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("participant_code,language,channel,score,created_at,q1")
    assert "P01" in lines[1] and "100.0" in lines[1]
