"""Compare several LLMs on the same clinical vignettes.

Usage:
    .venv/bin/python -m scripts.compare_models [eval/models.json] [eval/vignettes.csv]

Outputs:
    eval/comparison.csv — every model's prediction for every vignette
    eval/comparison.md  — accuracy, emergency sensitivity, per-language
                          accuracy and latency, side by side

Why this exists: triage accuracy for Nigerian Pidgin, Hausa and Yoruba
is not something you can look up. Running the identical vignette set
through several models under identical prompts and scoring makes the
comparison the study's own evidence rather than a claim borrowed from
someone else's benchmark.

Each model is configured in models.json:

    [{"name": "GPT-4o",
      "model": "gpt-4o",
      "base_url": "",                     // blank = OpenAI direct
      "api_key_env": "OPENAI_API_KEY"}]   // which .env key to use

A model whose key is missing is skipped with a note, so a partial run
still produces a usable table.
"""

import csv
import json
import os
import statistics
import sys
from pathlib import Path

from app import config, conversation
from scripts import evaluate


def load_models(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        models = json.load(f)
    if not isinstance(models, list) or not models:
        raise SystemExit(f"{path} must be a non-empty JSON list of model configs")
    for m in models:
        missing = {"name", "model"} - set(m)
        if missing:
            raise SystemExit(f"Model entry missing {missing}: {m}")
    return models


def _activate(model_cfg: dict) -> str | None:
    """Point the engine at this model. Returns an error string if the
    model cannot be used (missing key), else None."""
    key_env = model_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.getenv(key_env, "")
    if not api_key:
        return f"{key_env} not set in .env"

    config.OPENAI_API_KEY = api_key
    config.OPENAI_MODEL = model_cfg["model"]
    config.OPENAI_BASE_URL = model_cfg.get("base_url", "") or ""
    config.OPENAI_EXTRA_PARAMS = model_cfg.get("extra_params", {}) or {}
    # Parameter support differs per model — forget what the last one rejected.
    conversation._unsupported.clear()
    return None


def run_model(model_cfg: dict, vignettes: list[dict]) -> dict:
    """Run every vignette through one model."""
    problem = _activate(model_cfg)
    if problem:
        print(f"\n=== {model_cfg['name']} — SKIPPED ({problem}) ===")
        return {"name": model_cfg["name"], "skipped": problem, "rows": []}

    print(f"\n=== {model_cfg['name']} ({model_cfg['model']}) ===")
    rows = []
    for v in vignettes:
        predicted, seconds, turns = evaluate.run_vignette_timed(v)
        ok = "✓" if predicted == v["expected"] else "✗"
        print(
            f"  [{ok}] {v['id']:<14} {v['language']:<8} "
            f"expected {v['expected']:<10} got {predicted:<10} {seconds:5.1f}s"
        )
        rows.append(
            {
                "model": model_cfg["name"],
                "id": v["id"],
                "language": v["language"],
                "expected": v["expected"],
                "predicted": predicted,
                "seconds": round(seconds, 2),
                "turns": turns,
            }
        )
    return {"name": model_cfg["name"], "skipped": None, "rows": rows}


def summarise(result: dict) -> dict:
    """Chapter-5 metrics for one model, plus latency."""
    rows = result["rows"]
    metrics = evaluate.score(rows)
    latencies = [r["seconds"] for r in rows if r["predicted"] != "ERROR"]
    metrics["name"] = result["name"]
    metrics["median_seconds"] = round(statistics.median(latencies), 1) if latencies else None
    metrics["mean_turns"] = (
        round(statistics.mean(r["turns"] for r in rows), 1) if rows else None
    )
    metrics["errors"] = sum(1 for r in rows if r["predicted"] == "ERROR")
    return metrics


def render(summaries: list[dict], skipped: list[dict], vignette_count: int) -> str:
    def pct(x):
        return f"{x * 100:.1f}%" if x is not None else "n/a"

    languages = sorted({lang for s in summaries for lang in s["by_language"]})

    lines = [
        "# HealthBot NG — Model Comparison",
        "",
        f"Identical prompts, retrieval and scoring across all models; "
        f"{vignette_count} clinical vignettes.",
        "",
        "## Table 5.4 — Overall comparison",
        "",
        "| Model | Accuracy | Emergency sensitivity | Under-triage | Over-triage | Median latency | Mean turns | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['name']} | {pct(s['accuracy'])} | {pct(s['emergency_sensitivity'])} | "
            f"{s['under_triage']} | {s['over_triage']} | "
            f"{s['median_seconds'] if s['median_seconds'] is not None else 'n/a'}s | "
            f"{s['mean_turns'] if s['mean_turns'] is not None else 'n/a'} | {s['errors']} |"
        )

    lines += [
        "",
        f"Targets: accuracy ≥ {evaluate.TARGET_ACCURACY:.0%}, "
        f"emergency sensitivity ≥ {evaluate.TARGET_SENSITIVITY:.0%}.",
        "Under-triage is the unsafe direction — a patient told to stay home "
        "when they needed care. Weigh it above raw accuracy.",
        "",
        "## Table 5.5 — Accuracy by language",
        "",
        "| Model | " + " | ".join(l.capitalize() for l in languages) + " |",
        "|---" * (len(languages) + 1) + "|",
    ]
    for s in summaries:
        cells = []
        for lang in languages:
            stats = s["by_language"].get(lang)
            cells.append(
                f"{pct(stats['correct'] / stats['total'])} ({stats['correct']}/{stats['total']})"
                if stats
                else "—"
            )
        lines.append(f"| {s['name']} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Table 5.6 — Emergency detection detail",
        "",
        "| Model | Emergency vignettes | Detected | Missed | Sensitivity |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        missed = s["emergency_total"] - s["emergency_detected"]
        lines.append(
            f"| {s['name']} | {s['emergency_total']} | {s['emergency_detected']} | "
            f"{missed} | {pct(s['emergency_sensitivity'])} |"
        )

    if skipped:
        lines += ["", "## Not evaluated", ""]
        for s in skipped:
            lines.append(f"- **{s['name']}** — {s['skipped']}")

    lines += [
        "",
        "## Method",
        "",
        "- Every model received the identical system prompt, the identical "
        "retrieved protocol context and the identical vignette turns.",
        "- The deterministic red-flag safety net runs before any model call, "
        "so vignettes containing an explicit danger-sign phrase are decided "
        "identically by every model. Differences between models therefore "
        "come from the cases requiring clinical judgement.",
        "- A verdict of PENDING after the final nudge, or an API error, "
        "scores as incorrect.",
        "- Latency is wall-clock time for the whole conversation, including "
        "retrieval, measured from this machine.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    models_path = sys.argv[1] if len(sys.argv) > 1 else "eval/models.json"
    vignettes_path = sys.argv[2] if len(sys.argv) > 2 else "eval/vignettes.csv"

    models = load_models(models_path)
    vignettes = evaluate.load_vignettes(vignettes_path)
    print(f"Comparing {len(models)} models on {len(vignettes)} vignettes...")

    results = [run_model(m, vignettes) for m in models]
    evaluated = [r for r in results if not r["skipped"]]
    skipped = [r for r in results if r["skipped"]]
    if not evaluated:
        raise SystemExit("\nNo models could be evaluated — check the keys in .env")

    out_dir = Path(vignettes_path).parent
    with open(out_dir / "comparison.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["model", "id", "language", "expected", "predicted", "seconds", "turns"]
        )
        writer.writeheader()
        for r in evaluated:
            writer.writerows(r["rows"])

    summaries = [summarise(r) for r in evaluated]
    report = render(summaries, skipped, len(vignettes))
    (out_dir / "comparison.md").write_text(report, encoding="utf-8")
    print(f"\n{report}")
    print(f"Wrote {out_dir}/comparison.csv and {out_dir}/comparison.md")


if __name__ == "__main__":
    main()
