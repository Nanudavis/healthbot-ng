"""Chapter 5 evaluation: run clinical vignettes through the real triage
engine and produce Tables 5.1–5.3.

Usage:
    .venv/bin/python -m scripts.evaluate [eval/vignettes.csv]

Outputs:
    eval/results.csv — per-vignette prediction vs expected
    eval/results.md  — accuracy, emergency sensitivity, per-language tables

Method notes (state these in the report):
- Each vignette is a scripted patient: its messages are sent in order to
  a fresh session; the engine's questions are "answered" by the next
  scripted message. The first non-PENDING verdict is the prediction.
- If the engine is still asking questions after the script runs out, one
  neutral nudge is sent; a verdict of PENDING after that scores as wrong.
- Requires OPENAI_API_KEY. Red-flag vignettes resolve deterministically
  and work even without it.
"""

import csv
import sys
import time
from pathlib import Path

from app import conversation
from app.triage import TriageLevel

NUDGE = "That is all the information I have. Please decide now."
SEVERITY = {"SELF_CARE": 0, "CLINIC": 1, "EMERGENCY": 2}
TARGET_ACCURACY = 0.85
TARGET_SENSITIVITY = 0.95


def load_vignettes(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No vignettes in {path}")
    for row in rows:
        row["messages"] = [m.strip() for m in row["messages"].split("||") if m.strip()]
        if row["expected"] not in SEVERITY:
            raise SystemExit(f"Vignette {row['id']}: bad expected level {row['expected']!r}")
        if not row["messages"]:
            raise SystemExit(f"Vignette {row['id']}: no messages")
    return rows


def run_vignette_detailed(vignette: dict) -> dict:
    """Run one vignette, keeping the whole exchange.

    The transcript is what makes a wrong answer investigable: without it
    a misclassification is just a number, and the report cannot say
    whether the model never asked about duration or asked and reasoned
    badly. Vignettes are synthetic, so nothing here is patient data.
    """
    session_id = conversation.store.anonymise(f"eval:{vignette['id']}")
    conversation.store.reset(session_id)
    started = time.perf_counter()
    transcript: list[dict] = []

    def ask(message: str) -> object:
        result = conversation.classify_turn(session_id, message, vignette["language"])
        transcript.append(
            {
                "user": message,
                "bot": result.reply,
                "level": result.level.value,
                "reason": result.reason,
            }
        )
        return result

    predicted = "PENDING"
    try:
        for message in vignette["messages"]:
            result = ask(message)
            if result.level != TriageLevel.PENDING:
                predicted = result.level.value
                break
        else:
            predicted = ask(NUDGE).level.value
    except Exception as exc:
        print(f"  vignette {vignette['id']}: ERROR ({exc})")
        predicted = "ERROR"
        transcript.append({"user": "(failed)", "bot": str(exc)[:200], "level": "ERROR", "reason": ""})
    finally:
        conversation.store.reset(session_id)

    return {
        "predicted": predicted,
        "seconds": time.perf_counter() - started,
        "turns": len(transcript),
        "transcript": transcript,
    }


def run_vignette_timed(vignette: dict) -> tuple[str, float, int]:
    """(predicted level, wall-clock seconds, turns used).

    Latency matters for the deployment claim: WhatsApp users are on
    metered data and USSD gateways time out, so how long a decision
    takes is a result, not an implementation detail.
    """
    d = run_vignette_detailed(vignette)
    return d["predicted"], d["seconds"], d["turns"]


def run_vignette(vignette: dict) -> str:
    """Predicted level, or PENDING/ERROR (both score as incorrect)."""
    return run_vignette_timed(vignette)[0]


def render_transcripts(rows: list[dict]) -> str:
    """Every exchange, failures first — the error-analysis appendix."""
    wrong = [r for r in rows if r["predicted"] != r["expected"]]
    right = [r for r in rows if r["predicted"] == r["expected"]]

    lines = [
        "# Evaluation transcripts",
        "",
        "Full exchanges from the evaluation run. Vignettes are synthetic, "
        "so no patient data appears here.",
        "",
        f"**{len(wrong)} misclassified**, {len(right)} correct.",
        "",
        "## Misclassifications",
        "",
        "Read these to answer *why* a case failed — whether the engine "
        "never asked a decisive question, or asked and judged badly.",
        "",
    ]
    if not wrong:
        lines.append("_None._\n")
    for r in wrong:
        lines += _transcript_block(r, flag=True)

    lines += ["## Correct classifications", ""]
    for r in right:
        lines += _transcript_block(r, flag=False)
    return "\n".join(lines)


def _transcript_block(row: dict, flag: bool) -> list[str]:
    direction = ""
    if flag and row["predicted"] in SEVERITY and row["expected"] in SEVERITY:
        direction = (
            " · **under-triage (unsafe direction)**"
            if SEVERITY[row["predicted"]] < SEVERITY[row["expected"]]
            else " · over-triage (safe direction)"
        )
    head = (
        f"### {row['id']} ({row['language']}) — "
        f"expected {row['expected']}, got {row['predicted']}{direction}"
    )
    lines = [head, ""]
    for i, turn in enumerate(row.get("transcript", []), start=1):
        lines.append(f"{i}. **Patient:** {turn['user']}")
        lines.append(f"   **Bot [{turn['level']}]:** {turn['bot']}")
        if turn["reason"]:
            lines.append(f"   _reason: {turn['reason']}_")
    lines.append("")
    return lines


def score(rows: list[dict]) -> dict:
    """rows: [{id, language, expected, predicted}] → all Chapter 5 metrics.

    Infrastructure failures are counted separately from clinical
    mistakes. A rate-limited or timed-out call says nothing about the
    model's triage judgement, and folding it into accuracy would report
    a throttled run as a less accurate model.
    """
    total = len(rows)
    correct = sum(1 for r in rows if r["predicted"] == r["expected"])
    errors = [r for r in rows if r["predicted"] == "ERROR"]
    scored = [r for r in rows if r["predicted"] != "ERROR"]

    by_language: dict[str, dict] = {}
    for r in rows:
        lang = by_language.setdefault(r["language"], {"correct": 0, "total": 0})
        lang["total"] += 1
        lang["correct"] += r["predicted"] == r["expected"]

    levels = ["SELF_CARE", "CLINIC", "EMERGENCY"]
    confusion = {e: {p: 0 for p in [*levels, "PENDING", "ERROR"]} for e in levels}
    for r in rows:
        confusion[r["expected"]][r["predicted"]] += 1

    emergencies = [r for r in rows if r["expected"] == "EMERGENCY"]
    detected = sum(1 for r in emergencies if r["predicted"] == "EMERGENCY")

    over = under = 0
    for r in rows:
        if r["predicted"] in SEVERITY and r["predicted"] != r["expected"]:
            if SEVERITY[r["predicted"]] > SEVERITY[r["expected"]]:
                over += 1
            else:
                under += 1

    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "errors": len(errors),
        "error_ids": [r["id"] for r in errors],
        "scored": len(scored),
        # Accuracy over the vignettes that actually reached the model.
        "accuracy_excluding_errors": (
            sum(1 for r in scored if r["predicted"] == r["expected"]) / len(scored)
            if scored
            else None
        ),
        "by_language": by_language,
        "confusion": confusion,
        "emergency_total": len(emergencies),
        "emergency_detected": detected,
        "emergency_sensitivity": detected / len(emergencies) if emergencies else None,
        "over_triage": over,
        "under_triage": under,
    }


def render_report(m: dict) -> str:
    def pct(x):
        return f"{x * 100:.1f}%" if x is not None else "n/a"

    def check(ok):
        return "✅ target met" if ok else "❌ below target"

    lines = [
        "# HealthBot NG — Vignette Evaluation Results",
        "",
        "## Table 5.1 — Overall triage accuracy",
        "",
        "| Metric | Value | Target | Status |",
        "|---|---|---|---|",
        f"| Vignettes evaluated | {m['total']} | 50 | {'✅' if m['total'] >= 50 else '⚠ fewer than 50'} |",
        f"| Correct triage | {m['correct']}/{m['total']} | — | |",
        f"| **Overall accuracy** | **{pct(m['accuracy'])}** | ≥ 85% | {check(m['accuracy'] >= TARGET_ACCURACY)} |",
        f"| API failures (not clinical errors) | {m['errors']} | 0 | "
        + ("✅" if not m["errors"] else "⚠ see note below")
        + " |",
        f"| Over-triage (safe direction) | {m['over_triage']} | — | |",
        f"| Under-triage (unsafe direction) | {m['under_triage']} | — | |",
        "",
    ]
    if m["errors"]:
        lines += [
            f"> ⚠ **{m['errors']} vignette(s) never reached the model** "
            f"({', '.join(m['error_ids'][:8])}"
            + (", …" if len(m["error_ids"]) > 8 else "")
            + "). These are API failures — rate limits, timeouts — not clinical "
            "mistakes, but they are counted as incorrect in the accuracy above. "
            f"Excluding them, accuracy over the {m['scored']} vignettes that were "
            f"actually triaged is **{pct(m['accuracy_excluding_errors'])}**. "
            "Re-run before reporting: a throttled run understates the model.",
            "",
        ]
    lines += [
        "## Table 5.2 — Emergency detection",
        "",
        "| Metric | Value | Target | Status |",
        "|---|---|---|---|",
        f"| Emergency vignettes | {m['emergency_total']} | — | |",
        f"| Detected as EMERGENCY | {m['emergency_detected']} | — | |",
        f"| **Sensitivity** | **{pct(m['emergency_sensitivity'])}** | ≥ 95% | "
        + (
            check(m["emergency_sensitivity"] >= TARGET_SENSITIVITY)
            if m["emergency_sensitivity"] is not None
            else "n/a"
        )
        + " |",
        "",
        "### Confusion matrix (rows = expected, columns = predicted)",
        "",
        "| Expected \\ Predicted | SELF_CARE | CLINIC | EMERGENCY | PENDING | ERROR |",
        "|---|---|---|---|---|---|",
    ]
    for expected, row in m["confusion"].items():
        lines.append(
            f"| {expected} | {row['SELF_CARE']} | {row['CLINIC']} | "
            f"{row['EMERGENCY']} | {row['PENDING']} | {row['ERROR']} |"
        )
    lines += [
        "",
        "## Table 5.3 — Accuracy by language",
        "",
        "| Language | Correct | Total | Accuracy |",
        "|---|---|---|---|",
    ]
    for lang, s in sorted(m["by_language"].items()):
        lines.append(f"| {lang} | {s['correct']} | {s['total']} | {pct(s['correct'] / s['total'])} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "eval/vignettes.csv"
    vignettes = load_vignettes(path)
    print(f"Running {len(vignettes)} vignettes through the triage engine...")

    results = []
    for v in vignettes:
        detail = run_vignette_detailed(v)
        predicted = detail["predicted"]
        ok = "✓" if predicted == v["expected"] else "✗"
        print(f"  [{ok}] {v['id']} ({v['language']}): expected {v['expected']}, got {predicted}")
        results.append(
            {
                "id": v["id"],
                "language": v["language"],
                "expected": v["expected"],
                "predicted": predicted,
                "seconds": round(detail["seconds"], 2),
                "turns": detail["turns"],
                "transcript": detail["transcript"],
            }
        )

    out_dir = Path(path).parent
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "language", "expected", "predicted", "seconds", "turns"]
        )
        writer.writeheader()
        writer.writerows({k: r[k] for k in writer.fieldnames} for r in results)

    (out_dir / "transcripts.md").write_text(render_transcripts(results), encoding="utf-8")

    metrics = score(results)
    report = render_report(metrics)
    (out_dir / "results.md").write_text(report, encoding="utf-8")
    print(f"\n{report}")
    print(f"Wrote {out_dir}/results.csv, {out_dir}/results.md and {out_dir}/transcripts.md")


if __name__ == "__main__":
    main()
