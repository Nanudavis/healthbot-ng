# Deploying HealthBot NG on Railway

This guide supports the publicly hosted **academic demonstration**. It does not
describe a certified clinical deployment.

## Deploy from GitHub

1. Fork or clone the public repository and select the intended release tag or
   deployment branch.
2. In Railway, choose **New Project → Deploy from GitHub repo** and select the
   repository. `railway.json` selects the Dockerfile builder and `/health`
   liveness check.
3. Generate a public domain and set the environment variables below.

## Required configuration

Set secrets in the Railway service dashboard. Never commit them.

```text
ADMIN_TOKEN=<strong rotated token>
CONSOLE_AUTH_REQUIRED=true
DATABASE_URL=sqlite:////data/healthbot.db
PUBLIC_BASE_URL=<generated Railway domain>
SESSION_STORE=memory
STORE_TRANSCRIPTS=false
SEED_SAMPLE_FACILITIES=true

# Configure one supported model provider.
OPENAI_API_KEY=<provider key>
OPENAI_BASE_URL=<blank for OpenAI or an approved OpenAI-compatible /v1 URL>
OPENAI_MODEL=<currently available model identifier>

# Configure embeddings and the protocol index.
EMBEDDING_PROVIDER=hf
HF_API_TOKEN=<Hugging Face token>
HF_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
PROTOCOLS_DIR=/data/protocols
LOCAL_INDEX_PATH=/data/index/protocols.local.json

# Required for the live Twilio webhook.
TWILIO_AUTH_TOKEN=<Twilio authentication token>
```

Provider catalogues change. Confirm the selected model identifier with the
provider before deployment instead of relying on an old alias from this guide.
The optional `EMBEDDING_PROVIDER=local` path requires installation from
`requirements-local-embeddings.txt` and substantially increases the container
image because it includes the PyTorch sentence-transformer stack.

## Persistent volume

Mount a Railway volume at `/data` for the application database, uploaded
protocols and generated local index. Repository-default data paths are
ephemeral inside a container.

```bash
railway volume add --mount-path /data
```

## Verification

Every push or pull request runs the Python test suite and dashboard build
through GitHub Actions. A successful
`/health` response is only a liveness signal; it does not prove that the
database, retrieval provider, model provider, WhatsApp or USSD channel is
ready.

Before a defence demonstration, verify those dependencies separately and use
synthetic inputs only.

## Scale-up boundary

PostgreSQL, database-backed sessions and a managed vector index are available
configuration paths, but operational scaling does not establish clinical
safety. Strict retrieval grounding, formal privacy governance, facility-data
quality controls and independent clinical evaluation remain prerequisites for
external clinical use.
