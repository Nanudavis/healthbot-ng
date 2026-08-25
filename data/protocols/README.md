# Protocol documents (RAG corpus)

Real clinical protocol sources, downloaded 2026-07-05:

These documents remain the property of their issuing institutions and are
excluded from the repository's project-code licence. See
`THIRD_PARTY_NOTICES.md`.

| File | Source | Why it's here |
|---|---|---|
| `who_imci_chart_booklet_2014.pdf` | [WHO IMCI chart booklet, March 2014](https://www.who.int/publications/m/item/integrated-management-of-childhood-illness---chart-booklet-(march-2014)) | Core child triage: general danger signs, classify → refer/clinic/home |
| `who_etat_guideline_2016.pdf` | [WHO Paediatric ETAT guideline, 2016](https://www.who.int/publications/i/item/9789241510219) | Emergency triage of critically ill children |
| `who_etat_participant_manual.pdf` | [WHO AFRO ETAT training manual](https://www.afro.who.int/sites/default/files/2017-06/participant_manual.pdf) | Practical emergency sign assessment |
| `nigeria_ncdc_standard_case_definitions.pdf` | [NCDC standard case definitions](https://ncdc.gov.ng/themes/common/docs/protocols/31_1503912332.pdf) | Nigeria-official case definitions for priority diseases |
| `nigeria_fmoh_idsr_technical_guidelines.pdf` | [FMOH/NCDC IDSR technical guidelines, 3rd ed.](https://ncdc.gov.ng/themes/common/docs/protocols/242_1601639437.pdf) | FMOH surveillance + case definitions (615 pp) |

Known issue: a minority of IMCI chart-booklet text runs extract garbled
(broken font ToUnicode map in the WHO PDF itself); the danger-sign and
classification tables extract cleanly.

Rebuild the index after any change here:

```bash
python -m scripts.ingest
```

Configure the embedding provider and its required credentials in `.env`.
Pinecone is optional; without it, the application can write a local JSON index.

Still worth adding if you can obtain them (no public direct PDFs found):
FMOH National Standing Orders for CHEWs, and the FMOH National
Guidelines for Diagnosis and Treatment of Malaria (2020, 4th ed.).
