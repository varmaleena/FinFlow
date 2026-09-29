# FINFLOW architecture

React + TypeScript (Vite) communicates with FastAPI over `/v1`. In the built local app, FastAPI serves both the UI and API on port 8000. SQLAlchemy persists JSON records for transactions, settlements, policy decisions, cases and audit events, with a separate unique-key action table. SQLite is the local default; PostgreSQL is supported through `DATABASE_URL` and Docker Compose.

This MVP uses a compact record schema rather than the guide's fully normalized production schema. `Base.metadata.create_all` bootstraps a fresh database; a versioned migration system is not yet included. Run one API worker for the local demo: a process lock serializes mutations and the database enforces action idempotency. Multi-worker job leasing and production identity authentication are not implemented.

## Deterministic boundary

`domain/money_twin.py` reconstructs payment state. Missing evidence and contradictions fail closed. `domain/policy_engine.py` allows only bounded notification retries and pending monitors. Execution checks policy again against current evidence. High-risk financial actions are denied. `domain/financial.py` uses Decimal for settlement and amortization calculations.

The synthetic notification executor persists an acknowledgement before claiming success. Pending monitors durably track checks and escalate after two unresolved observations. The periodic local worker resumes RUNNING monitors after restart. Audit events retain evidence IDs, trace IDs, policy IDs, idempotency keys and workflow IDs. Cases retain the evidence observed at investigation time; updated Money Twin state records verification.

## Provider boundaries

- Default language routing is deterministic English/Hindi keyword routing, with grounded templates. `LLM_API_KEY` enables an OpenAI-compatible intent-only adapter. Model output is restricted to an intent enum and cannot execute writes or supply canonical financial facts. Provider failures fall back to local routing. Model integration needs a valid endpoint, key and model to test live.
- Sarvam REST STT and TTS adapters use server-side keys. Short recording is capped at 15 seconds in the UI and 5 MB at upload. Without a key, the UI offers browser dictation and browser speech synthesis; browser support and microphone permission vary. English and Hindi payment templates are supplied; non-payment explanations are currently English.
- `services/cognee.py` supplies optional Cognee ingestion and retrieval. Local playbooks work without credentials. Enable Cognee only after installing its optional package and configuring its model/embedding providers, then run `python -c "import asyncio; from apps.api.app.services.cognee import seed_memory; asyncio.run(seed_memory())"`. Retrieval is available at `/v1/tools/memory?query=...`; retrieved prose cannot override payment state or policy. It is not injected into generated financial answers.
- Local execution is visibly labeled `local:act_...`. For n8n, import the workflow export, configure its inbound Header Auth credential (`X-Finflow-Secret` matching `WORKFLOW_SECRET`), activate it and set `N8N_WEBHOOK_URL` to its production webhook URL. It calls protected internal step endpoints, waits, retries reads and escalates. Outbound internal requests use `WORKFLOW_SECRET` from the n8n environment. Successful n8n dispatch does not itself mark an action successful.

## Demo scope

All merchants and evidence are synthetic. No real money movement, loan underwriting, actual lender callback, or external support notification happens. Escalation is a durable review case plus audit evidence, visible in Case history. Credit studio exposes assumptions and computes scenarios but does not claim eligibility or approval. Shared demo-token authentication provides a local access gate, not individual merchant identity; the merchant selector is an intentional demo feature. Do not expose this demo publicly without replacing authentication, adding tenant authorization, rate limiting, migration management and durable multi-worker workflow leases.

## Provider references

- https://docs.sarvam.ai/api-reference/speech-to-text/transcribe
- https://docs.sarvam.ai/api-reference/text-to-speech/convert
- https://docs.cognee.ai/core-concepts/main-operations/legacy-operations/search
