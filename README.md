# HealthBot NG

**A Multilingual AI-Powered Health Triage and Care-Routing System for Primary Healthcare Access in Nigeria**

[![CI](https://github.com/Nanudavis/healthbot-ng/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanudavis/healthbot-ng/actions/workflows/ci.yml)

HealthBot NG is a professional master's project from Miva Open University. It
explores multilingual symptom intake, conservative triage and care-level
routing through WhatsApp, USSD and a web-based research console.

> **Academic scope:** HealthBot NG is a research prototype, not a certified
> medical device or clinical service. Its controlled vignette results do not
> establish safety or effectiveness for real patients. It must not be used to
> diagnose, prescribe or replace professional care.

- **Live academic demonstration:** <https://healthbot-ng-production.up.railway.app>
- **Defence release:** <https://github.com/Nanudavis/healthbot-ng/releases/tag/v1.0-masters-defence>

## Implemented capabilities

- English, Nigerian Pidgin, Hausa, Yoruba and Igbo interaction paths.
- Three care levels: `SELF_CARE`, `CLINIC` and `EMERGENCY`.
- A deterministic multilingual red-flag layer that runs before the model.
- Retrieval-augmented generation over selected FMOH, NCDC and WHO protocols.
- WhatsApp webhook integration and deterministic USSD menus.
- Sample facility routing using geographic distance and care level.
- A research analytics console, native-language review workflow and SUS
  questionnaire workflow.
- Authenticated administration, observability, migrations and automated tests.

The configured red-flag matcher provides a reproducible safeguard for the
phrases it recognises; it cannot guarantee detection of every emergency
expression, spelling, dialect or code-switched formulation. Retrieval failure
currently permits an ungrounded model path. Strict grounding and independent
clinical validation are mandatory before any external clinical use.

## Architecture

| Layer | Main responsibilities |
|---|---|
| Access | Twilio WhatsApp webhook, Africa's Talking USSD, web console |
| Reasoning | Conversation control, language handling, LLM parsing, deterministic escalation |
| Knowledge | RAG over a bounded protocol corpus using a hosted or local index |
| Data | SQLAlchemy persistence for settings, facilities, pseudonymised events and study workflows |

## Controlled evaluation

The final development/regression battery contains 56 synthetic,
protocol-derived vignettes. The same cases were run twice, producing 112
descriptive decisions. Reference levels were researcher-assigned, and the
battery informed system refinement; it is not a held-out clinical-validation
dataset.

| Metric | Verified result |
|---|---:|
| Run 1 exact accuracy | 42/56 (75.0%) |
| Run 2 exact accuracy | 43/56 (76.8%) |
| Pooled exact accuracy | 85/112 (75.9%) |
| Repeat prediction agreement | 55/56 (98.2%) |
| Emergency recall | 40/40 (100.0%) |
| Emergency-vs-non-emergency specificity | 71/72 (98.6%) |
| Non-emergency exact-class accuracy | 45/72 (62.5%) |
| Self-care recall | 10/36 (27.8%) |
| Clinic recall | 35/36 (97.2%) |
| Standard classwise macro-F1 | 71.5% |
| Over-triage | 27/112 (24.1%) |
| Under-triage | 0/112 (0.0%) |

The pooled `n=112` is a repeated-decision count, not 112 independent patients.
All 20 emergency cases in each run were explicit danger-sign cases intercepted
by the tuned deterministic layer. The 40/40 result is therefore an in-set
regression check, not proof of clinical sensitivity.

An initial native-language review recorded 117 Hausa, Yoruba and Igbo items:
114 were accepted and 3 were corrected. This involved one reviewer per
language. Pidgin, complete-interface coverage and multi-rater agreement remain
outstanding. No participant-derived SUS score or independent clinician-labelled
validation result is claimed.

See [the evaluation note](docs/EVALUATION.md) and
[the reproducibility guide](docs/REPRODUCIBILITY.md) for the retained inputs,
calculation procedure and interpretation boundaries.

## Repository layout

```text
app/                  FastAPI application, channel integrations and triage logic
dashboard/            React source for the research analytics console
data/                 Sample facility data and bounded protocol corpus
eval/                 Synthetic vignettes and retained defence-run decisions
scripts/              Selected ingestion, evaluation and reproducibility tools
tests/                Automated Python test suite
docs/                 Evaluation and reproducibility documentation
```

## Run locally

Python 3.11 or 3.12 and Node.js 22 are recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

The application can start without AI credentials, keeping deterministic safety
features available. LLM conversation and protocol retrieval require the
corresponding provider settings in `.env`. Set a strong `ADMIN_TOKEN` before
using the console.

The default installation uses hosted embeddings and avoids the large
PyTorch-based local-model stack. To run sentence-transformer embeddings fully
offline, install `requirements-local-embeddings.txt` instead and set
`EMBEDDING_PROVIDER=local`:

```bash
python -m pip install -r requirements-local-embeddings.txt
```

Build the dashboard:

```bash
cd dashboard
npm ci
npm run build
```

Run the automated checks:

```bash
python -m pytest tests -q
python scripts/recompute_final_metrics.py
```

At the final local audit, the suite reported **532 passing tests**. Automated
tests support software-correctness and regression claims; they do not replace
live-channel, provider, usability or clinical validation.

## Data and privacy boundaries

- `.env`, local databases, transcripts and generated indexes are excluded from
  version control.
- Direct phone numbers are not stored in the normal triage-event path. Stable
  hashes remain linkable and are therefore **pseudonymous**, not anonymous.
- Transcript persistence is disabled by default. Channel and AI providers can
  still process message data in transit.
- The facility registry is sample data and must be replaced with an authorised,
  quality-controlled registry before operational use.
- No reviewer-identifiable export, genuine participant dataset or patient record
  is included in this repository.

## Deployment

The Railway deployment instructions describe an academic demonstration, not a
production clinical service. See [DEPLOYMENT.md](DEPLOYMENT.md).

## Citation

Use the tagged defence release and the metadata in [CITATION.cff](CITATION.cff).
The changing `main` branch should not be used as the reproducibility reference
for the submitted report.

## Licence and third-party material

The project code is released for non-commercial academic review under the
[Academic Review Source Licence](LICENSE). Clinical protocol documents remain
the property of their issuing institutions and are excluded from that licence;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
