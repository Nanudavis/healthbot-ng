#!/usr/bin/env python3
"""Recompute the final pooled vignette metrics from retained CSV runs.

The script deliberately distinguishes three-class exact agreement from the
binary EMERGENCY-versus-non-EMERGENCY screen.  This prevents the 45/72
non-emergency exact-match rate from being mislabeled as binary specificity.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


LEVELS = ("SELF_CARE", "CLINIC", "EMERGENCY")
ORDER = {level: i for i, level in enumerate(LEVELS)}


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def _metric(num: int, den: int) -> dict:
    return {"numerator": num, "denominator": den, "rate": _ratio(num, den)}


def load_run(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    required = {"id", "language", "expected", "predicted"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path}: expected columns {sorted(required)}")
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError(f"{path}: duplicate vignette id {row['id']}")
        seen.add(row["id"])
        if row["expected"] not in LEVELS or row["predicted"] not in LEVELS:
            raise ValueError(f"{path}: invalid level in {row['id']}")
    return rows


def compute(paths: list[Path]) -> dict:
    if len(paths) < 2:
        raise ValueError("At least two retained runs are required")
    runs = [load_run(path) for path in paths]
    baseline = {row["id"]: row for row in runs[0]}
    for path, rows in zip(paths[1:], runs[1:]):
        current = {row["id"]: row for row in rows}
        if set(current) != set(baseline):
            raise ValueError(f"{path}: vignette ids differ from the first run")
        for vignette_id, first in baseline.items():
            other = current[vignette_id]
            if (other["language"], other["expected"]) != (
                first["language"], first["expected"]
            ):
                raise ValueError(f"{path}: source fields changed for {vignette_id}")

    pooled = [row for rows in runs for row in rows]
    matrix = {
        expected: {predicted: 0 for predicted in LEVELS} for expected in LEVELS
    }
    for row in pooled:
        matrix[row["expected"]][row["predicted"]] += 1

    correct = sum(matrix[level][level] for level in LEVELS)
    over = sum(
        1 for row in pooled if ORDER[row["predicted"]] > ORDER[row["expected"]]
    )
    under = sum(
        1 for row in pooled if ORDER[row["predicted"]] < ORDER[row["expected"]]
    )

    class_metrics = {}
    for level in LEVELS:
        tp = matrix[level][level]
        actual = sum(matrix[level].values())
        predicted = sum(matrix[expected][level] for expected in LEVELS)
        precision = _ratio(tp, predicted)
        recall = _ratio(tp, actual)
        f1 = _ratio(2 * precision * recall, precision + recall)
        class_metrics[level] = {
            "support": actual,
            "predicted": predicted,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    macro_precision = sum(v["precision"] for v in class_metrics.values()) / 3
    macro_recall = sum(v["recall"] for v in class_metrics.values()) / 3
    macro_f1 = sum(v["f1"] for v in class_metrics.values()) / 3

    emergency_tp = matrix["EMERGENCY"]["EMERGENCY"]
    emergency_fn = sum(matrix["EMERGENCY"][p] for p in LEVELS if p != "EMERGENCY")
    emergency_fp = sum(matrix[e]["EMERGENCY"] for e in LEVELS if e != "EMERGENCY")
    emergency_tn = sum(
        matrix[e][p]
        for e in LEVELS
        for p in LEVELS
        if e != "EMERGENCY" and p != "EMERGENCY"
    )
    non_emergency_exact = matrix["SELF_CARE"]["SELF_CARE"] + matrix["CLINIC"]["CLINIC"]
    non_emergency_total = sum(
        sum(matrix[e].values()) for e in ("SELF_CARE", "CLINIC")
    )

    run_summaries = []
    for path, rows in zip(paths, runs):
        run_correct = sum(row["expected"] == row["predicted"] for row in rows)
        run_summaries.append(
            {
                "file": str(path),
                "correct": run_correct,
                "total": len(rows),
                "accuracy": _ratio(run_correct, len(rows)),
            }
        )

    prediction_vectors = defaultdict(list)
    for rows in runs:
        for row in rows:
            prediction_vectors[row["id"]].append(row["predicted"])
    repeat_agree = sum(len(set(values)) == 1 for values in prediction_vectors.values())

    by_language = {}
    language_rows = defaultdict(list)
    for row in pooled:
        language_rows[row["language"]].append(row)
    for language, rows in sorted(language_rows.items()):
        hits = sum(row["expected"] == row["predicted"] for row in rows)
        by_language[language] = _metric(hits, len(rows))

    direction_counts = Counter(
        f"{row['expected']}->{row['predicted']}"
        for row in pooled
        if row["expected"] != row["predicted"]
    )

    return {
        "source_runs": [str(path) for path in paths],
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
        },
        "unique_vignettes": len(baseline),
        "evaluation_runs": len(runs),
        "pooled_decisions": len(pooled),
        "run_summaries": run_summaries,
        "repeat_prediction_agreement": _metric(repeat_agree, len(baseline)),
        "confusion_matrix": matrix,
        "overall_exact_accuracy": _metric(correct, len(pooled)),
        "over_triage": _metric(over, len(pooled)),
        "under_triage": _metric(under, len(pooled)),
        "class_metrics": class_metrics,
        "macro": {
            "precision": macro_precision,
            "recall": macro_recall,
            "f1": macro_f1,
        },
        "emergency_binary": {
            "tp": emergency_tp,
            "fn": emergency_fn,
            "fp": emergency_fp,
            "tn": emergency_tn,
            "sensitivity": _ratio(emergency_tp, emergency_tp + emergency_fn),
            "specificity": _ratio(emergency_tn, emergency_tn + emergency_fp),
            "positive_predictive_value": _ratio(emergency_tp, emergency_tp + emergency_fp),
            "negative_predictive_value": _ratio(emergency_tn, emergency_tn + emergency_fn),
        },
        "non_emergency_exact_class_accuracy": _metric(
            non_emergency_exact, non_emergency_total
        ),
        "error_directions": dict(sorted(direction_counts.items())),
        "by_language": by_language,
        "interpretation_note": (
            "The two runs repeat the same 56 vignettes. Pooled n=112 is a descriptive "
            "decision count, not 112 independent clinical cases."
        ),
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def to_markdown(result: dict) -> str:
    matrix = result["confusion_matrix"]
    lines = [
        "# HealthBot NG — independently recomputed final metrics",
        "",
        f"Sources: `{result['source_runs'][0]}` and `{result['source_runs'][1]}`.",
        "",
        f"- Unique vignettes: **{result['unique_vignettes']}**",
        f"- Repeated runs: **{result['evaluation_runs']}**",
        f"- Pooled decisions: **{result['pooled_decisions']}**",
        f"- Repeat prediction agreement: **{result['repeat_prediction_agreement']['numerator']}/{result['repeat_prediction_agreement']['denominator']} "
        f"({_pct(result['repeat_prediction_agreement']['rate'])})**",
        *[
            f"- `{run['file']}`: **{run['correct']}/{run['total']} ({_pct(run['accuracy'])})**"
            for run in result["run_summaries"]
        ],
        "",
        "## Pooled confusion matrix",
        "",
        "| Reference \\ Predicted | SELF_CARE | CLINIC | EMERGENCY |",
        "|---|---:|---:|---:|",
    ]
    for expected in LEVELS:
        lines.append(
            f"| {expected} | {matrix[expected]['SELF_CARE']} | "
            f"{matrix[expected]['CLINIC']} | {matrix[expected]['EMERGENCY']} |"
        )
    overall = result["overall_exact_accuracy"]
    self_care = result["class_metrics"]["SELF_CARE"]
    clinic = result["class_metrics"]["CLINIC"]
    emergency = result["emergency_binary"]
    non_emergency = result["non_emergency_exact_class_accuracy"]
    lines.extend(
        [
            "",
            "## Correctly labelled metrics",
            "",
            "| Metric | Result |",
            "|---|---:|",
            f"| Three-class exact accuracy | {overall['numerator']}/{overall['denominator']} ({_pct(overall['rate'])}) |",
            f"| Emergency sensitivity | {emergency['tp']}/{emergency['tp'] + emergency['fn']} ({_pct(emergency['sensitivity'])}) |",
            f"| Emergency-vs-non-emergency specificity | {emergency['tn']}/{emergency['tn'] + emergency['fp']} ({_pct(emergency['specificity'])}) |",
            f"| Non-emergency exact-class accuracy | {non_emergency['numerator']}/{non_emergency['denominator']} ({_pct(non_emergency['rate'])}) |",
            f"| Self-care recall | {matrix['SELF_CARE']['SELF_CARE']}/{self_care['support']} ({_pct(self_care['recall'])}) |",
            f"| Clinic recall | {matrix['CLINIC']['CLINIC']}/{clinic['support']} ({_pct(clinic['recall'])}) |",
            f"| Macro precision | {_pct(result['macro']['precision'])} |",
            f"| Macro recall | {_pct(result['macro']['recall'])} |",
            f"| Standard macro-F1 (mean of class F1 values) | {_pct(result['macro']['f1'])} |",
            f"| Over-triage, conservative direction | {result['over_triage']['numerator']}/{result['over_triage']['denominator']} ({_pct(result['over_triage']['rate'])}) |",
            f"| Under-triage | {result['under_triage']['numerator']}/{result['under_triage']['denominator']} ({_pct(result['under_triage']['rate'])}) |",
            "",
            "> The formerly reported 45/72 (62.5%) is non-emergency exact-class accuracy, not binary specificity. The standard emergency-screen specificity is 71/72 (98.6%).",
            "",
            f"> {result['interpretation_note']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "runs",
        nargs="*",
        type=Path,
        default=[
            Path("eval/retained/defence-run-1.csv"),
            Path("eval/retained/defence-run-2.csv"),
        ],
    )
    parser.add_argument("--json-out", type=Path, default=Path("eval/final_metrics.json"))
    parser.add_argument(
        "--markdown-out", type=Path, default=Path("eval/final_metrics.md")
    )
    args = parser.parse_args()
    paths = args.runs or [
        Path("eval/retained/defence-run-1.csv"),
        Path("eval/retained/defence-run-2.csv"),
    ]
    result = compute(paths)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    markdown = to_markdown(result)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.write_text(markdown + "\n", encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
