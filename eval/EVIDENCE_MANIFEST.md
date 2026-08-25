# Final evaluation evidence manifest

Prepared for the public master's-defence release on 2026-08-25.

## Public retained artefacts

| Artefact | SHA-256 |
|---|---|
| `vignettes.csv` | `9175762019854420ca1abb4e57dc5ea808bad325eac8c08695d38acba2c1161c` |
| `retained/defence-run-1.csv` | `9e193e0c80dabfa83e725df875454597b6bd988608a747244d855b9f57507d04` |
| `retained/defence-run-2.csv` | `c239ad526d1d1ff58d9151bb9facbe9368514ab8b5c0df430bee68cb2b960260` |

The two run files contain synthetic case decisions only. Credentials,
transcripts, reviewer-identifiable exports, local databases and genuine
participant data are excluded from the public release.

## Verified summary

- Run 1: 42/56 exact matches (75.0%).
- Run 2: 43/56 exact matches (76.8%).
- Repeat prediction agreement: 55/56 (98.2%).
- Pooled exact accuracy: 85/112 (75.9%).
- Emergency-vs-non-emergency specificity: 71/72 (98.6%).
- Non-emergency exact-class accuracy: 45/72 (62.5%).
- Macro-F1: 71.5%.
- Over-triage: 27/112; under-triage: 0/112.

Recalculate with:

```bash
python scripts/recompute_final_metrics.py
```

## Reproducibility limitation

The raw CSV schema does not record every provider/model version, prompt hash,
protocol-index hash, execution timestamp or runtime setting. The tagged
release supports audited recalculation and procedural repeatability, not exact
replay of the external model service. See `docs/REPRODUCIBILITY.md`.
