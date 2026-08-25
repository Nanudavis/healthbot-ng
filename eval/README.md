# Controlled vignette evaluation (Chapter 4)

`vignettes.csv` contains the 56-case development set: 13 English, 13
Nigerian Pidgin, 12 Hausa, 12 Yoruba and 6 Igbo cases; 20 EMERGENCY,
18 CLINIC and 18 SELF_CARE reference levels. The cases are synthetic and
protocol-derived, and their reference levels are researcher-assigned.

The same battery informed prompt, red-flag and timeout refinement before the
final runs. The results are therefore post-optimisation development/regression
evidence—not held-out clinical validation or an estimate of real-patient
performance. Independent clinician confirmation and a new held-out battery
remain outstanding.

Initial native-speaker review covered selected Hausa, Yoruba and Igbo safety
phrases and vignette texts. Pidgin, multi-rater agreement and broader interface
coverage remain gaps.

## Format

CSV columns:

- `id` — unique case identifier, for example `ha-emerg-3`
- `language` — english | pidgin | hausa | yoruba | igbo
- `expected` — SELF_CARE | CLINIC | EMERGENCY
- `messages` — scripted patient turns separated by `||`

## Run

```bash
# Requires the selected model credentials and an ingested protocol corpus.
.venv/bin/python -m scripts.evaluate eval/vignettes.csv
```

The final evidence consists of two repeat runs of the same 56 cases. The
decision files are retained as:

- `eval/retained/defence-run-1.csv`
- `eval/retained/defence-run-2.csv`

The 112 pooled decisions are repeated measurements, not 112 independent
clinical cases. The public files contain synthetic case decisions only; model
transcripts and reviewer-identifiable material are not published.

Recompute the canonical summary:

```bash
python scripts/recompute_final_metrics.py
```

This writes `final_metrics.md` and `final_metrics.json`. Verified results are:

- run accuracy: 43/56 (76.8%) and 42/56 (75.0%)
- descriptive pooled accuracy: 85/112 (75.9%)
- run-to-run prediction agreement: 55/56 (98.2%)
- emergency recall: 40/40 (100.0%) across the repeat decisions
- self-care recall: 10/36 (27.8%)
- emergency-vs-non-emergency specificity: 71/72 (98.6%)
- non-emergency exact-class accuracy: 45/72 (62.5%)
- standard classwise macro-F1: 71.5%
- over-triage: 27/112 (24.1%); under-triage: 0/112

The distinction between 98.6% binary specificity and 62.5% exact-tier accuracy
among non-emergency decisions is important. They answer different questions.

## Interpretation boundaries

All 20 explicit emergency cases are intercepted by the tuned deterministic
red-flag layer before a model call. Its 20/20 outcome is an in-set regression
check, not an independent estimate of clinical sensitivity. Twenty-six of the
27 pooled errors arise when the minimum-two-turn guard overrides an initial
SELF_CARE output to CLINIC. This is a measurable safety–utility trade-off,
not simply an LLM classification failure.

The final evaluation harness uses a 60-second model timeout to reduce provider
timeout noise; the live webhook default is 12 seconds. The clinical logic is
shared, but these runs do not establish deployed latency or channel reliability.

## Model comparison

```bash
.venv/bin/python -m scripts.compare_models
```

This optional script runs available configured models over the same development
set. Explicit danger-sign cases are decided before a model call, so model
differences arise only on the remaining cases. Do not interpret this as a
held-out comparison, and report missing-provider or timeout failures exactly.
