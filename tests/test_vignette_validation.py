"""Clinical validation workflow for the AI-drafted evaluation vignettes."""

import pytest
from fastapi.testclient import TestClient

from app import config, db, vignettes
from app.main import app

client = TestClient(app)

CSV = """id,language,expected,messages
v-1,english,EMERGENCY,"Chest pain and sweating"
v-2,pidgin,CLINIC,"my pikin dey hot||e never reach 2 years"
v-3,hausa,SELF_CARE,"ina jin ciwon kai kadan"
"""


@pytest.fixture
def vig_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path}/vig.db")
    db.reset_engine()
    db.init_db()
    path = tmp_path / "v.csv"
    path.write_text(CSV, encoding="utf-8")
    vignettes.import_csv(str(path))
    yield tmp_path
    db.reset_engine()


def test_import_parses_multi_turn_messages(vig_db):
    rows = {v["vignette_id"]: v for v in vignettes.list_all()}
    assert len(rows) == 3
    assert rows["v-2"]["messages"] == ["my pikin dey hot", "e never reach 2 years"]
    assert rows["v-2"]["proposed_level"] == "CLINIC"
    assert rows["v-2"]["validations"] == []


def test_import_rejects_bad_level(tmp_path, vig_db):
    bad = tmp_path / "bad.csv"
    bad.write_text("id,language,expected,messages\nx,english,MAYBE,hello\n", encoding="utf-8")
    with pytest.raises(ValueError):
        vignettes.import_csv(str(bad))


def test_reimport_preserves_existing_verdicts(vig_db, tmp_path):
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    updated = tmp_path / "v2.csv"
    updated.write_text(
        'id,language,expected,messages\nv-1,english,CLINIC,"Chest pain, revised wording"\n',
        encoding="utf-8",
    )
    vignettes.import_csv(str(updated))
    row = next(v for v in vignettes.list_all() if v["vignette_id"] == "v-1")
    assert row["proposed_level"] == "CLINIC"        # draft text refreshed
    assert row["consensus_level"] == "EMERGENCY"    # clinician verdict kept
    assert row["validations"][0]["validator"] == "Dr A"


def test_validate_records_who_and_when(vig_db):
    result = vignettes.validate("v-1", "EMERGENCY", "Dr E. Mkpojiogu", "classic ACS")
    assert result["consensus_level"] == "EMERGENCY"
    verdict = result["validations"][0]
    assert verdict["validator"] == "Dr E. Mkpojiogu"
    assert verdict["at"] is not None
    assert verdict["notes"] == "classic ACS"
    assert result["agrees"] is True


def test_validate_requires_a_named_validator(vig_db):
    """An unattributed verdict is worthless as an audit trail."""
    with pytest.raises(ValueError, match="validated_by"):
        vignettes.validate("v-1", "EMERGENCY", "   ")


def test_validate_rejects_bad_level(vig_db):
    with pytest.raises(ValueError):
        vignettes.validate("v-1", "PROBABLY_FINE", "Dr A")


def test_validate_unknown_id_returns_none(vig_db):
    assert vignettes.validate("nope", "CLINIC", "Dr A") is None


def test_correction_is_tracked_as_disagreement(vig_db):
    result = vignettes.validate("v-3", "CLINIC", "Dr A", "elderly patient, review")
    assert result["agrees"] is False
    p = vignettes.progress()
    assert p["changed"] == 1
    assert p["corrections"][0] == {
        "vignette_id": "v-3",
        "from": "SELF_CARE",
        "to": "CLINIC",
        "notes": "elderly patient, review",
    }


def test_progress_reports_agreement_rate(vig_db):
    assert vignettes.progress()["agreement_rate"] is None  # nothing validated yet
    vignettes.validate("v-1", "EMERGENCY", "Dr A")  # agrees
    vignettes.validate("v-2", "EMERGENCY", "Dr A")  # corrected
    p = vignettes.progress()
    assert p["validated"] == 2 and p["pending"] == 1
    assert p["agreement_rate"] == 0.5
    assert p["validators"] == ["Dr A"]
    assert p["by_language"]["english"] == {"total": 1, "validated": 1}


def test_export_excludes_unvalidated_by_default(vig_db, tmp_path):
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    out = tmp_path / "validated.csv"
    count = vignettes.export_validated(str(out))
    assert count == 1
    body = out.read_text()
    assert "v-1" in body and "v-2" not in body


def test_export_uses_the_clinician_label_not_the_draft(vig_db, tmp_path):
    vignettes.validate("v-3", "CLINIC", "Dr A")  # drafted SELF_CARE
    out = tmp_path / "validated.csv"
    vignettes.export_validated(str(out))
    row = [l for l in out.read_text().splitlines() if l.startswith("v-3")][0]
    assert "CLINIC" in row and "SELF_CARE" not in row


def test_exported_file_feeds_the_evaluation_harness(vig_db, tmp_path):
    """The export must be loadable by scripts.evaluate unchanged."""
    from scripts import evaluate

    vignettes.validate("v-2", "CLINIC", "Dr A")
    out = tmp_path / "validated.csv"
    vignettes.export_validated(str(out))
    loaded = evaluate.load_vignettes(str(out))
    assert loaded[0]["messages"] == ["my pikin dey hot", "e never reach 2 years"]
    assert loaded[0]["expected"] == "CLINIC"


# ── Endpoints ───────────────────────────────────────────────────

def test_api_list_and_progress(vig_db):
    assert len(client.get("/api/vignettes").json()) == 3
    assert client.get("/api/vignettes/progress").json()["pending"] == 3


def test_api_validate(vig_db):
    r = client.post(
        "/api/vignettes/v-1/validate",
        data={"level": "EMERGENCY", "validated_by": "Dr A", "notes": "ok"},
    )
    assert r.status_code == 200
    assert r.json()["agrees"] is True


def test_api_validate_missing_validator_is_rejected(vig_db):
    r = client.post("/api/vignettes/v-1/validate", data={"level": "CLINIC", "validated_by": ""})
    assert r.status_code == 400


def test_api_validate_unknown_id_404s(vig_db):
    r = client.post(
        "/api/vignettes/nope/validate", data={"level": "CLINIC", "validated_by": "Dr A"}
    )
    assert r.status_code == 404


def test_api_import_upload(vig_db):
    csv_bytes = b'id,language,expected,messages\nv-9,yoruba,CLINIC,"omo mi ni iba"\n'
    r = client.post(
        "/api/vignettes/import",
        files={"file": ("v.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["added"] == 1
    assert any(v["vignette_id"] == "v-9" for v in vignettes.list_all())


def test_api_import_rejects_malformed_csv(vig_db):
    r = client.post(
        "/api/vignettes/import",
        files={"file": ("v.csv", b"id,language,expected,messages\nx,en,NONSENSE,hi\n", "text/csv")},
    )
    assert r.status_code == 400


def test_api_export_csv(vig_db):
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    r = client.get("/api/vignettes/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["X-Vignette-Count"] == "1"
    assert "v-1" in r.text


# ── Multiple raters and inter-rater reliability ─────────────────

def test_two_raters_agreeing_gives_consensus(vig_db):
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    result = vignettes.validate("v-1", "EMERGENCY", "Dr B")
    assert result["consensus_level"] == "EMERGENCY"
    assert result["disputed"] is False
    assert len(result["validations"]) == 2


def test_two_raters_disagreeing_has_no_consensus(vig_db):
    """A disputed vignette has no defensible gold label until the
    raters resolve it — it must not silently take one rater's answer."""
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    result = vignettes.validate("v-1", "CLINIC", "Dr B")
    assert result["disputed"] is True
    assert result["consensus_level"] is None
    assert result["agrees"] is None


def test_disputed_vignettes_are_excluded_from_export(vig_db, tmp_path):
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    vignettes.validate("v-1", "CLINIC", "Dr B")       # disputed
    vignettes.validate("v-2", "CLINIC", "Dr A")
    vignettes.validate("v-2", "CLINIC", "Dr B")       # agreed
    out = tmp_path / "validated.csv"
    assert vignettes.export_validated(str(out)) == 1
    body = out.read_text()
    assert "v-2" in body and "v-1" not in body


def test_a_rater_can_change_their_own_verdict(vig_db):
    vignettes.validate("v-1", "CLINIC", "Dr A")
    result = vignettes.validate("v-1", "EMERGENCY", "Dr A")
    assert len(result["validations"]) == 1  # updated, not duplicated
    assert result["consensus_level"] == "EMERGENCY"


def test_progress_reports_pairwise_kappa(vig_db):
    for vid, a, b in [
        ("v-1", "EMERGENCY", "EMERGENCY"),
        ("v-2", "CLINIC", "CLINIC"),
        ("v-3", "SELF_CARE", "SELF_CARE"),
    ]:
        vignettes.validate(vid, a, "Dr A")
        vignettes.validate(vid, b, "Dr B")
    p = vignettes.progress()
    assert p["validators"] == ["Dr A", "Dr B"]
    assert p["per_validator"] == {"Dr A": 3, "Dr B": 3}
    pair = p["pairwise_kappa"][0]
    assert pair["raters"] == ["Dr A", "Dr B"]
    assert pair["n"] == 3
    assert pair["kappa"] == 1.0
    assert pair["interpretation"] == "almost perfect"


def test_disputes_are_listed_for_adjudication(vig_db):
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    vignettes.validate("v-1", "CLINIC", "Dr B")
    p = vignettes.progress()
    assert p["disputed"] == 1
    assert p["disputes"][0]["verdicts"] == {"Dr A": "EMERGENCY", "Dr B": "CLINIC"}


def test_kappa_needs_shared_items(vig_db):
    """Raters who scored different vignettes share no basis for kappa."""
    vignettes.validate("v-1", "EMERGENCY", "Dr A")
    vignettes.validate("v-2", "CLINIC", "Dr B")
    pair = vignettes.progress()["pairwise_kappa"][0]
    assert pair["n"] == 0
    assert pair["kappa"] is None


# ── Kappa arithmetic ────────────────────────────────────────────

def test_kappa_reference_values():
    assert vignettes.cohens_kappa([("A", "A"), ("B", "B"), ("C", "C"), ("A", "A")]) == 1.0
    # Both raters split 50/50 and agree half the time = chance level.
    assert vignettes.cohens_kappa([("Y", "Y"), ("Y", "N"), ("N", "Y"), ("N", "N")]) == 0.0
    # Perfect disagreement.
    assert vignettes.cohens_kappa([("Y", "N"), ("N", "Y"), ("Y", "N"), ("N", "Y")]) == -1.0


def test_kappa_undefined_for_single_item():
    assert vignettes.cohens_kappa([("A", "A")]) is None
    assert vignettes.cohens_kappa([]) is None


def test_kappa_when_only_one_category_used():
    """Chance agreement is 1.0 here, so the formula divides by zero.
    Perfect agreement is what happened, so report 1.0."""
    assert vignettes.cohens_kappa([("A", "A")] * 5) == 1.0


def test_kappa_paradox_is_reported_faithfully():
    """High raw agreement with a dominant category can still give a
    near-zero or negative kappa. The number is not smoothed over."""
    pairs = [("A", "A")] * 8 + [("A", "B"), ("B", "A")]
    kappa = vignettes.cohens_kappa(pairs)
    assert kappa < 0
    assert vignettes.kappa_interpretation(kappa) == "poor"


@pytest.mark.parametrize(
    ("kappa", "label"),
    [(0.9, "almost perfect"), (0.7, "substantial"), (0.5, "moderate"),
     (0.3, "fair"), (0.1, "slight"), (-0.2, "poor")],
)
def test_kappa_interpretation_bands(kappa, label):
    assert vignettes.kappa_interpretation(kappa) == label
