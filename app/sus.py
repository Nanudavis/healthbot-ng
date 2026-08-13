"""System Usability Scale (Brooke, 1996) capture and scoring.

The thesis target is a mean SUS above 68 — the accepted "average"
threshold — from roughly 20 participants. Scoring is done here rather
than by hand because the alternating-polarity arithmetic is easy to get
wrong on paper, and a transcription error would silently corrupt a
graded result.

Scoring: odd-numbered items contribute (answer - 1), even-numbered items
contribute (5 - answer); the sum is multiplied by 2.5 to give 0-100.
"""

import statistics
from datetime import datetime, timezone

from sqlalchemy import select

from app import db
from app.models import SusResponse

# Standard SUS wording, lightly adapted to name the system. Odd items
# are positively worded, even items negatively — the alternation is
# part of the instrument and must not be "tidied up".
ITEMS = (
    "I think that I would like to use HealthBot NG frequently.",
    "I found HealthBot NG unnecessarily complex.",
    "I thought HealthBot NG was easy to use.",
    "I think I would need help from someone technical to use HealthBot NG.",
    "I found the different parts of HealthBot NG worked well together.",
    "I thought there was too much inconsistency in HealthBot NG.",
    "I would imagine most people would learn to use HealthBot NG very quickly.",
    "I found HealthBot NG very awkward to use.",
    "I felt confident using HealthBot NG.",
    "I needed to learn a lot before I could get going with HealthBot NG.",
)
TARGET_SCORE = 68.0  # accepted average; the thesis target is above this


def score_answers(answers: list[int]) -> float:
    """SUS score (0-100) for one completed questionnaire."""
    if len(answers) != 10:
        raise ValueError(f"SUS needs exactly 10 answers, got {len(answers)}")
    if any(not isinstance(a, int) or a < 1 or a > 5 for a in answers):
        raise ValueError("Every SUS answer must be an integer from 1 to 5")
    total = 0
    for index, answer in enumerate(answers):
        # index 0 is item 1 (odd, positively worded)
        total += (answer - 1) if index % 2 == 0 else (5 - answer)
    return round(total * 2.5, 1)


def grade(score: float) -> str:
    """Sauro & Lewis curved grade — useful shorthand in the write-up."""
    if score >= 80.3:
        return "A (excellent)"
    if score >= 68:
        return "B (good)"
    if score >= 51:
        return "C (OK)"
    if score >= 35:
        return "D (poor)"
    return "F (unacceptable)"


def record(
    participant_code: str,
    answers: list[int],
    language: str = "english",
    channel: str = "whatsapp",
    comments: str = "",
) -> dict:
    value = score_answers(answers)
    if not participant_code.strip():
        raise ValueError("participant_code is required")
    db.init_db()
    with db.get_session() as session:
        row = SusResponse(
            participant_code=participant_code.strip()[:40],
            language=language.strip().lower()[:16] or "english",
            channel=channel.strip().lower()[:16] or "whatsapp",
            answers=",".join(str(a) for a in answers),
            score=value,
            comments=(comments or "").strip()[:1000],
        )
        session.add(row)
        session.commit()
        return {
            "participant_code": row.participant_code,
            "score": value,
            "grade": grade(value),
        }


def summary() -> dict:
    """Aggregate results, including the per-item means that show which
    aspects of usability scored poorly."""
    with db.get_session() as session:
        rows = session.scalars(select(SusResponse).order_by(SusResponse.created_at)).all()
        responses = [
            {
                "participant_code": r.participant_code,
                "language": r.language,
                "channel": r.channel,
                "answers": [int(a) for a in r.answers.split(",") if a],
                "score": r.score,
                "comments": r.comments or "",
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    scores = [r["score"] for r in responses]
    if not scores:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "std_dev": None,
            "min": None,
            "max": None,
            "grade": None,
            "meets_target": None,
            "target": TARGET_SCORE,
            "item_means": [],
            "by_language": {},
            "by_channel": {},
            "responses": [],
        }

    def group(key: str) -> dict:
        out: dict[str, dict] = {}
        for r in responses:
            entry = out.setdefault(r[key], {"n": 0, "scores": []})
            entry["n"] += 1
            entry["scores"].append(r["score"])
        return {
            k: {"n": v["n"], "mean": round(statistics.mean(v["scores"]), 1)}
            for k, v in out.items()
        }

    item_means = [
        {
            "item": index + 1,
            "text": ITEMS[index],
            "mean": round(statistics.mean(r["answers"][index] for r in responses), 2),
            "positive": index % 2 == 0,
        }
        for index in range(10)
    ]

    mean = round(statistics.mean(scores), 1)
    return {
        "n": len(scores),
        "mean": mean,
        "median": round(statistics.median(scores), 1),
        "std_dev": round(statistics.stdev(scores), 1) if len(scores) > 1 else 0.0,
        "min": min(scores),
        "max": max(scores),
        "grade": grade(mean),
        "meets_target": mean > TARGET_SCORE,
        "target": TARGET_SCORE,
        "item_means": item_means,
        "by_language": group("language"),
        "by_channel": group("channel"),
        "responses": responses,
    }
