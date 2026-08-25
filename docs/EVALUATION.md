# Controlled evaluation evidence

## Status

The retained evidence is a tuned development/regression battery, not a
held-out clinical-validation study. It contains 56 synthetic,
protocol-derived vignettes with researcher-assigned reference levels. The same
cases were run twice.

The two retained decision files are:

- `eval/retained/defence-run-1.csv`
- `eval/retained/defence-run-2.csv`

They contain synthetic vignette identifiers, languages, reference levels,
predictions, elapsed seconds and turn counts. They contain no patient records
or participant identifiers.

## Verified pooled confusion matrix

| Reference \ Predicted | SELF_CARE | CLINIC | EMERGENCY |
|---|---:|---:|---:|
| SELF_CARE | 10 | 26 | 0 |
| CLINIC | 0 | 35 | 1 |
| EMERGENCY | 0 | 0 | 40 |

## Correctly labelled metrics

| Metric | Result |
|---|---:|
| Three-class exact accuracy | 85/112 (75.9%) |
| Emergency sensitivity/recall | 40/40 (100.0%) |
| Emergency-vs-non-emergency specificity | 71/72 (98.6%) |
| Non-emergency exact-class accuracy | 45/72 (62.5%) |
| Self-care recall | 10/36 (27.8%) |
| Clinic recall | 35/36 (97.2%) |
| Standard classwise macro-F1 | 71.5% |
| Over-triage | 27/112 (24.1%) |
| Under-triage | 0/112 (0.0%) |

The value 45/72 is exact agreement on the self-care/clinic tier among
non-emergency decisions. It is not binary specificity. Only one
non-emergency decision was predicted as emergency, so binary specificity is
71/72.

## Interpretation boundaries

- `n=112` represents repeated decisions on 56 cases, not 112 independent
  patients.
- The battery informed prompt, guard and timeout refinement.
- All explicit emergency cases were intercepted by the tuned deterministic
  red-flag layer. The 40/40 outcome is an in-set regression check.
- Twenty-six of the 27 errors were SELF_CARE-to-CLINIC escalations; one was a
  CLINIC-to-EMERGENCY escalation.
- The results do not establish real-patient safety, diagnostic accuracy,
  population effectiveness or regulatory readiness.
- Independent clinician labelling, a held-out case set, inter-rater agreement
  and genuine participant usability evaluation remain outstanding.

## Native-language review

The project report records an initial review of 117 Hausa, Yoruba and Igbo
items: 114 accepted and 3 corrected, with one reviewer per language. This is
bounded translation review, not comprehensive linguistic validation. Pidgin,
complete-interface coverage and multi-rater agreement remain future work.
Reviewer-identifiable exports are intentionally excluded from the public
repository.

## Usability

The application implements a SUS questionnaire workflow, but the defence
release does not claim a participant-derived SUS score because no retained
genuine participant dataset substantiates one.
