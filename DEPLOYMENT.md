# Deploying HealthBot NG on Railway

This deployment keeps HealthBot NG online continuously: Railway runs the
FastAPI service and its PostgreSQL service stores records and session state.

## Required Railway services

1. Create a new Railway project from this folder or its GitHub repository.
2. Add a **PostgreSQL** service.
3. Add this application as a service. Railway uses the included `Dockerfile`.
4. Generate a public domain for the application service.

## Required application variables

Set these in the application service, never in the repository:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
SESSION_STORE=db
CONSOLE_AUTH_REQUIRED=true
ADMIN_TOKEN=<a long, unique secret>
PUBLIC_BASE_URL=https://<your-railway-domain>
OPENAI_API_KEY=<provider key>
OPENAI_MODEL=<your approved model>
```

Set the provider-specific variables as applicable (`OPENAI_BASE_URL`,
`EMBEDDING_*`, and `PINECONE_*`). For an initial demonstration only, set
`SEED_SAMPLE_FACILITIES=true`; replace the sample data with an approved FMOH
facility registry before any public health use.

## Channel configuration

After the Railway health check passes, set webhook URLs to:

```text
Twilio WhatsApp:  https://<your-railway-domain>/webhook/whatsapp
Africa's Talking: https://<your-railway-domain>/webhook/ussd
```

Set `TWILIO_AUTH_TOKEN` before enabling the Twilio webhook signature check.
Use only one web worker unless a shared session store and a shared RAG index
have both been configured; this deployment uses the database session store.

## Verification

```bash
curl https://<your-railway-domain>/health
```

Expect `{"status":"ok","service":"healthbot-ng"}`. Then open
`/dashboard`, log in with `ADMIN_TOKEN`, and test both webhook endpoints.
