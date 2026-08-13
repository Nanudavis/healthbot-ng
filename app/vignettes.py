"""Clinical validation of evaluation vignettes, with inter-rater
reliability.

The 50 vignettes are AI-drafted, so their labels are proposals until
clinicians review them. This module holds that review workflow.

Two or more independent raters are supported deliberately: a single
rater's opinion cannot be checked, and single-rater validation is a
recognised weakness in clinical vignette studies. With two raters the
study can report Cohen's kappa, and disagreements are surfaced for
adjudication instead of being averaged away.
"""

import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app import db
from app.models import ClinicalVignette, VignetteValidation

LEVELS = ("SELF_CARE", "CLINIC", "EMERGENCY")

# Landis & Koch (1977) bands, the convention for reporting kappa.
KAPPA_BANDS = (
    (0.81, "almost perfect"),
    (0.61, "substantial"),
    (0.41, "moderate"),
    (0.21, "fair"),
    (0.01, "slight"),
    (-1.0, "poor"),
)


def cohens_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for paired categorical ratings.

    Returns None when fewer than two items are jointly rated. When both
    raters use only one category and agree on everything, chance
    agreement is 1.0 and kappa is undefined — 1.0 is returned, since
    perfect agreement is what actually happened.
    """
    if len(pairs) < 2:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n

    a_counts = Counter(a for a, _ in pairs)
    b_counts = Counter(b for _, b in pairs)
    expected = sum(
        (a_counts.get(level, 0) / n) * (b_counts.get(level, 0) / n)
        for level in set(a_counts) | set(b_counts)
    )
    if expected >= 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 3)


def kappa_interpretation(kappa: float | None) -> str | None:
    if kappa is None:
        return None
    for threshold, label in KAPPA_BANDS:
        if kappa >= threshold:
            return label
    return "poor"


def import_csv(csv_path: str) -> dict:
    """Load drafted vignettes. Re-importing updates the draft text but
    never discards verdicts clinicians have already recorded."""
    db.init_db()
    added = updated = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with db.get_session() as session:
        for row in rows:
            level = (row.get("expected") or "").strip().upper()
            if level not in LEVELS:
                raise ValueError(f"{row.get('id')}: bad expected level {level!r}")
            existing = session.scalar(
                select(ClinicalVignette).where(
                    ClinicalVignette.vignette_id == row["id"].strip()
                )
            )
            if existing:
                existing.language = row["language"].strip()
                existing.messages = row["messages"].strip()
                existing.proposed_level = level
                updated += 1
            else:
                session.add(
                    ClinicalVignette(
                        vignette_id=row["id"].strip(),
                        language=row["language"].strip(),
                        messages=row["messages"].strip(),
                        proposed_level=level,
                    )
                )
                added += 1
        session.commit()
    return {"added": added, "updated": updated, "total": added + updated}


def list_all() -> list[dict]:
    with db.get_session() as session:
        vignettes = session.scalars(
            select(ClinicalVignette).order_by(ClinicalVignette.vignette_id)
        ).all()
        validations = session.scalars(select(VignetteValidation)).all()

    by_vignette: dict[str, list] = {}
    for v in validations:
        by_vignette.setdefault(v.vignette_id, []).append(v)

    return [_as_dict(v, by_vignette.get(v.vignette_id, [])) for v in vignettes]


def validate(vignette_id: str, level: str, validated_by: str, notes: str = "") -> dict | None:
    """Record (or update) one clinician's verdict. Returns None if the
    vignette id is unknown."""
    level = level.strip().upper()
    if level not in LEVELS:
        raise ValueError(f"bad triage level {level!r}")
    validator = validated_by.strip()
    if not validator:
        raise ValueError("validated_by is required — the record must name the validator")

    with db.get_session() as session:
        vignette = session.scalar(
            select(ClinicalVignette).where(ClinicalVignette.vignette_id == vignette_id)
        )
        if vignette is None:
            return None
        existing = session.scalar(
            select(VignetteValidation).where(
                VignetteValidation.vignette_id == vignette_id,
                VignetteValidation.validator == validator[:120],
            )
        )
        if existing:
            existing.level = level
            existing.notes = (notes or "").strip()[:500]
            existing.created_at = datetime.now(timezone.utc)
        else:
            session.add(
                VignetteValidation(
                    vignette_id=vignette_id,
                    validator=validator[:120],
                    level=level,
                    notes=(notes or "").strip()[:500],
                )
            )
        session.commit()
        rows = session.scalars(
            select(VignetteValidation).where(VignetteValidation.vignette_id == vignette_id)
        ).all()
        return _as_dict(vignette, rows)


def progress() -> dict:
    """Coverage, agreement with the drafted labels, and inter-rater
    reliability between each pair of clinicians."""
    rows = list_all()
    total = len(rows)
    rated = [r for r in rows if r["validations"]]
    consensus = [r for r in rows if r["consensus_level"]]
    disputed = [r for r in rows if r["disputed"]]
    agreed_with_draft = [
        r for r in consensus if r["consensus_level"] == r["proposed_level"]
    ]

    validators = sorted({v["validator"] for r in rows for v in r["validations"]})
    per_validator = {
        name: sum(1 for r in rows if any(v["validator"] == name for v in r["validations"]))
        for name in validators
    }

    # Pairwise kappa, on the vignettes both raters scored.
    pairwise = []
    for i, first in enumerate(validators):
        for second in validators[i + 1 :]:
            pairs = []
            for r in rows:
                a = next((v["level"] for v in r["validations"] if v["validator"] == first), None)
                b = next((v["level"] for v in r["validations"] if v["validator"] == second), None)
                if a and b:
                    pairs.append((a, b))
            kappa = cohens_kappa(pairs)
            pairwise.append(
                {
                    "raters": [first, second],
                    "n": len(pairs),
                    "kappa": kappa,
                    "interpretation": kappa_interpretation(kappa),
                    "raw_agreement": (
                        round(sum(1 for a, b in pairs if a == b) / len(pairs), 3)
                        if pairs
                        else None
                    ),
                }
            )

    by_language: dict[str, dict] = {}
    for r in rows:
        entry = by_language.setdefault(r["language"], {"total": 0, "validated": 0})
        entry["total"] += 1
        if r["consensus_level"]:
            entry["validated"] += 1

    return {
        "total": total,
        "validated": len(consensus),
        "rated": len(rated),
        "pending": total - len(rated),
        "disputed": len(disputed),
        "agreed": len(agreed_with_draft),
        "changed": len(consensus) - len(agreed_with_draft),
        "agreement_rate": (
            round(len(agreed_with_draft) / len(consensus), 3) if consensus else None
        ),
        "validators": validators,
        "per_validator": per_validator,
        "pairwise_kappa": pairwise,
        "by_language": by_language,
        "corrections": [
            {
                "vignette_id": r["vignette_id"],
                "from": r["proposed_level"],
                "to": r["consensus_level"],
                "notes": " · ".join(v["notes"] for v in r["validations"] if v["notes"]),
            }
            for r in consensus
            if r["consensus_level"] != r["proposed_level"]
        ],
        "disputes": [
            {
                "vignette_id": r["vignette_id"],
                "verdicts": {v["validator"]: v["level"] for v in r["validations"]},
            }
            for r in disputed
        ],
    }


def export_validated(out_path: str, include_pending: bool = False) -> int:
    """Write a vignettes CSV using the clinicians' consensus label,
    ready for scripts.evaluate.

    Vignettes where raters disagree are excluded: an unresolved dispute
    has no defensible gold label, and silently picking one rater's
    answer would hide that.
    """
    rows = list_all()
    usable = [r for r in rows if r["consensus_level"] or include_pending]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "language", "expected", "messages"])
        writer.writeheader()
        for r in usable:
            writer.writerow(
                {
                    "id": r["vignette_id"],
                    "language": r["language"],
                    "expected": r["consensus_level"] or r["proposed_level"],
                    # Back to the "||" wire format scripts.evaluate parses.
                    "messages": "||".join(r["messages"]),
                }
            )
    return len(usable)


def _as_dict(v: ClinicalVignette, validations: list) -> dict:
    verdicts = [
        {
            "validator": row.validator,
            "level": row.level,
            "notes": row.notes or "",
            "at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in sorted(validations, key=lambda r: r.validator)
    ]
    levels = {row["level"] for row in verdicts}
    consensus = levels.pop() if len(levels) == 1 else None
    return {
        "vignette_id": v.vignette_id,
        "language": v.language,
        "messages": [m.strip() for m in v.messages.split("||") if m.strip()],
        "proposed_level": v.proposed_level,
        "validations": verdicts,
        "consensus_level": consensus,
        "disputed": len({row["level"] for row in verdicts}) > 1,
        "agrees": None if consensus is None else consensus == v.proposed_level,
    }
