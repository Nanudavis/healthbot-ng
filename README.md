# HealthBot NG

**A Multilingual AI-Powered Health Triage and Care-Routing System for Primary Healthcare Access in Nigeria**

Professional Master's Project (MIT) — Miva Open University, Abuja · School of Computing, Department of Information Technology.
Author: Osuji Chinanu Davidson (2025/A/MIT/0735) · Supervisor: Dr Emmanuel Mkpojiogu.

**Live system:** <https://healthbot-ng-production.up.railway.app> · **USSD:** `*347*88#` (Africa's Talking simulator) · **CI:** 531 automated tests passing

---

## What it does

Patients describe symptoms in **English, Nigerian Pidgin, Hausa, Yoruba or Igbo** via WhatsApp
or USSD and receive a triage verdict — `SELF_CARE`, `VISIT_CLINIC` or `EMERGENCY` — with
care-level routing to the nearest appropriate facility. Every reply is grounded in Federal
Ministry of Health / WHO clinical protocols through retrieval-augmented generation (RAG),
and a **deterministic safety net** guarantees that danger signs escalate and are never
downgraded by the model.

## Architecture (four layers, one FastAPI service)

| Layer | Responsibility |
|---|---|
| Access | Twilio WhatsApp webhook (signature-verified) · Africa's Talking USSD (`*347*88#`) · React surveillance dashboard |
| Reasoning | Conversation controller · language detection · LLM triage parsing with fail-safe escalation |
| Knowledge | RAG over FMOH IDSR/NCDC/WHO ETAT+IMCI protocols (2,822 chunks; Pinecone or local index) |
| Data | SQLAlchemy (SQLite dev / PostgreSQL prod) — anonymised sessions, facilities, SUS, audit tables |

A deterministic red-flag matcher runs **before every model call**: danger phrases in all five
languages trigger an immediate in-language emergency reply without any LLM involvement.
Unparseable model output escalates **up** to clinic level, never down.

## Evaluation highlights

| Metric | Result |
|---|---|
| Overall triage accuracy | **75.9%** (85/112 pooled dual-run decisions, CI 67.2–82.9%) |
| Emergency sensitivity | **100%** (40/40) — zero under-triage across all 112 decisions |
| Per-language accuracy | Pidgin 76.9% · Yoruba 79.2% · English 69.2% · Hausa 66.7% · Igbo 100% |
| Retrieval grounding | 12/12 protocol queries hit the expected source |
| Usability | SUS mean **74.6** ("good"), pilot N = 7 |

All Hausa, Yoruba and Igbo strings were independently reviewed by native speakers
(3 corrections applied to the deployed engine). Results are reported honestly: the 85%
accuracy target was not met — the error analysis shows conservative over-triage of mild
self-care cases is the sole cause, and it is the project's actionable contribution.

## Repository layout

```
app/            FastAPI application (webhooks, triage, RAG, dashboard API)
dashboard/      React + Recharts surveillance console (built dist served at /dashboard)
data/           Facility registry (sample), protocol PDFs, local vector index
eval/           Clinical vignette corpus + evaluation harness inputs
scripts/        Ingestion, seeding, evaluation, figure generation
tests/          Pytest suite (531 tests)
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # add your OPENAI_API_KEY (or DeepSeek key)

uvicorn app.main:app --reload --port 8000
```

Smoke test:

```bash
curl -X POST http://127.0.0.1:8000/webhook/whatsapp \
  --data-urlencode "Body=Abeg my pikin dey shake" \
  --data-urlencode "From=whatsapp:+2340000000000"
```

## Tests

```bash
pytest tests/ -q                    # 531 tests
```

## Security notes

- All secrets ship via environment variables (`.env` is gitignored; `.env.example` has blanks).
- Twilio webhook requests are HMAC-signature verified when `TWILIO_AUTH_TOKEN` is set.
- No PII is stored: sessions use anonymised identifiers; the dashboard serves aggregates only.
- The full seven-threat security model is documented in the project report (Table 3.4).

## License & attribution

Clinical protocols in `data/protocols/` belong to the Federal Ministry of Health (Nigeria),
NCDC and the World Health Organisation and are included for non-commercial educational use.
Sample facility data is synthetic; replace with the FMOH registry before production use.
