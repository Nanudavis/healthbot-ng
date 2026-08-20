"""Native-speaker validation form: content, endpoints, storage, export."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, review_items
from app.main import app

client = TestClient(app)


@pytest.fixture
def review_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/review.db")
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()


# ── Content ──────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["hausa", "yoruba", "igbo"])
def test_items_for_language_are_complete(lang):
    items = review_items.items_for(lang)
    keys = [i["key"] for i in items]
    # safety-critical texts always present
    assert "return_lead" in keys
    assert "return_signs_child" in keys and "return_signs_adult" in keys
    assert "emergency_override" in keys
    # red flags and vignettes present
    redflags = [k for k in keys if k.startswith("redflag_")]
    vigenettes = [k for k in keys if not k.startswith(("redflag_", "return_", "emergency_override"))]
    assert len(redflags) >= 15
    assert len(vigenettes) >= 6  # smallest pool is Igbo with 6
    for i in items:
        assert i["draft"].strip() and i["english"].strip()
        assert i["key"]


def test_markers_are_nonempty():
    for lang in ("hausa", "yoruba", "igbo"):
        m = review_items.markers(lang)
        assert m["words"] and m["phrases"]


# ── Endpoints ────────────────────────────────────────────────────

def test_page_renders_with_substituted_items():
    r = client.get("/native-review")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "__REVIEW_ITEMS__" not in r.text  # placeholder substituted
    assert "__MARKERS__" not in r.text
    assert "Part 1" in r.text and "Part 3" in r.text


def test_page_accepts_language_query():
    r = client.get("/native-review?language=yoruba")
    assert r.status_code == 200
    assert "yoruba" in r.text
    r = client.get("/native-review?language=klingon")
    assert r.status_code == 200  # falls back to hausa


def _items_payload():
    return json.dumps([
        {"item_id": "return_lead", "item_type": "string",
         "english": "lead-in", "draft": "JE YANZU IDAN:", "verdict": "ok"},
        {"item_id": "redflag_0", "item_type": "string",
         "english": "fits", "draft": "farfadiya",
         "verdict": "correction", "correction": "farfadiya (revised)"},
    ])


def test_submit_stores_rows(review_db):
    r = client.post(
        "/api/native-review",
        data={
            "language": "hausa",
            "reviewer_name": "Malam Ibrahim Musa",
            "reviewer_role": "Hausa teacher",
            "organisation": "",
            "assessment": "minor",
            "comments": "mostly good",
            "items": _items_payload(),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "saved" and body["count"] == 2

    session = db.get_session()
    try:
        from app import models
        rows = session.query(models.NativeReview).all()
        assert len(rows) == 2
        by_id = {r.item_id: r for r in rows}
        assert by_id["return_lead"].verdict == "ok"
        assert by_id["redflag_0"].verdict == "correction"
        assert by_id["redflag_0"].correction == "farfadiya (revised)"
        assert by_id["redflag_0"].reviewer_name == "Malam Ibrahim Musa"
    finally:
        session.close()


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        ({"language": "klingon"}, 400),
        ({"reviewer_name": "   "}, 400),
        ({"items": "not json"}, 400),
        ({"items": "[]"}, 400),
        ({"items": json.dumps([{"item_id": "x", "verdict": "maybe"}])}, 400),
        ({"items": json.dumps([
            {"item_id": "x", "verdict": "correction", "correction": ""}])}, 400),
    ],
)
def test_submit_rejects_bad_input(review_db, overrides, expected_status):
    data = {
        "language": "hausa",
        "reviewer_name": "A. Reviewer",
        "items": _items_payload(),
    }
    data.update(overrides)
    r = client.post("/api/native-review", data=data)
    assert r.status_code == expected_status


def test_export_csv(review_db):
    client.post(
        "/api/native-review",
        data={
            "language": "hausa",
            "reviewer_name": "Malam Ibrahim Musa",
            "items": _items_payload(),
        },
    )
    r = client.get("/api/export/native-review.csv")
    # export is console-gated; with auth required it must 401 unauthenticated
    if config.CONSOLE_AUTH_REQUIRED:
        assert r.status_code == 401
    else:
        assert r.status_code == 200
        assert "native_reviews.csv" in r.headers["content-disposition"]
        assert "Malam Ibrahim Musa" in r.text
