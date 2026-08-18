# Deploying HealthBot NG on Railway

This deployment keeps HealthBot NG online continuously. Railway runs the
FastAPI service (built from the included `Dockerfile`) with SQLite on a
persistent volume (`/data`); no PostgreSQL is required at this scale.

## Deploy from GitHub

1. Push this repository (private, branch `main`) to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo** → select the repo.
   The included `railway.json` selects the Dockerfile builder and sets the
   `/health` healthcheck.
3. Generate a public domain for the service.

## Required variables

Set these in the Railway service dashboard (never commit them):

```text
ADMIN_TOKEN=<rotated demo token, kept out of the thesis>
CONSOLE_AUTH_REQUIRED=true
DATABASE_URL=sqlite:////data/healthbot.db
EMBEDDING_PROVIDER=hf
HF_API_TOKEN=<Hugging Face token with Inference Providers permission>
HF_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
OPENAI_API_KEY=<DeepSeek key>
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
PUBLIC_BASE_URL=<the generated railway domain>
SEED_SAMPLE_FACILITIES=true
SESSION_STORE=memory
TWILIO_AUTH_TOKEN=<Twilio account auth token — enables signature verification>
```

## Persistent volume

`railway volume add --mount-path /data` — holds `healthbot.db` (records,
settings, SUS responses) across restarts and redeploys.

## Redeploying

Every `git push` to `main` triggers a build + deploy (~2 minutes). The CI
workflow runs the full test suite on push.

## Scale-up path

For multi-worker production: PostgreSQL (`DATABASE_URL`), `SESSION_STORE=db`,
and a managed vector index — the application supports all three behind
environment variables.
