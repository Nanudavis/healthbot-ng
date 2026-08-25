# Reproducibility guide

This guide reproduces the software checks and recalculates the retained
defence metrics. It does not reproduce a clinical trial.

## Environment

- Python 3.11 or 3.12
- Node.js 22
- A POSIX-like shell for the example commands

## Install and test

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest tests -q

cd dashboard
npm ci
npm run build
cd ..
```

The final local audit produced 532 passing Python tests and a successful Vite
production build. GitHub Actions reruns both checks from a clean environment.

## Recalculate the reported metrics

```bash
python scripts/recompute_final_metrics.py
```

The script reads:

- `eval/retained/defence-run-1.csv`
- `eval/retained/defence-run-2.csv`

and writes ignored local outputs `eval/final_metrics.json` and
`eval/final_metrics.md`.

Expected SHA-256 values:

```text
9e193e0c80dabfa83e725df875454597b6bd988608a747244d855b9f57507d04  defence-run-1.csv
c239ad526d1d1ff58d9151bb9facbe9368514ab8b5c0df430bee68cb2b960260  defence-run-2.csv
```

Expected key outputs are pooled exact accuracy 85/112 (75.9%),
emergency-vs-non-emergency specificity 71/72 (98.6%), macro-F1 71.5%,
over-triage 27/112 and under-triage 0/112.

## Re-running the external-model battery

```bash
cp .env.example .env
# Configure the selected model and retrieval providers in .env.
python -m scripts.ingest
python -m scripts.evaluate eval/vignettes.csv
```

External-model replay can vary with provider/model revisions, runtime
configuration and service availability. The original retained CSV files do
not machine-record every provider version, prompt hash or protocol-index hash.
The tagged release therefore supports audited recalculation and procedural
repeatability, not bit-for-bit reproduction of the external model responses.

## Data exclusions

The release excludes credentials, local databases, transcripts,
reviewer-identifiable exports and genuine participant records. The facility
registry is sample data, and the protocol corpus is bounded rather than a
complete Nigerian clinical decision-support library.
