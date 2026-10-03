# Live integration runbook

## Vertex AI: spry-catcher-509805-u4

Local `.env`:

```dotenv
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=spry-catcher-509805-u4
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.8-flash
```

Run Google Cloud **application-default login** and use the identity with access to the specified project. Enable the Vertex AI API and billing, and grant the runtime identity the Vertex AI User role. Set the ADC quota project to `spry-catcher-509805-u4` where applicable. The application uses OAuth credentials; it does not read or expose an API key for Vertex requests.

For hosted Cloud Run, use an attached least-privileged service account. Do not upload a service-account JSON key. The model is configurable because availability and lifecycle vary. The selected default is listed in Google's current [model lifecycle documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions).

Run `python -m scripts.check_vertex` after authentication. It sends one synthetic extraction and prints only the provider/model/result and whether fallback occurred. It does not print access tokens.

## n8n Cloud: varmaleena.app.n8n.cloud

1. Import `workflows/n8n/paytm-resolve.json` into the supplied workspace.
2. Create an **HTTP Header Auth** credential with name `X-Resolve-Secret` and a random private value. Assign it to the Webhook and all HTTP Request nodes. Keep this value in n8n's credential store, not the workflow JSON.
3. Set the n8n variable `RESOLVE_API_BASE` to the backend's reachable HTTPS origin without a trailing slash. n8n Cloud cannot call this laptop's `127.0.0.1` address.
4. On the backend, set `RESOLVE_WORKFLOW_SECRET` to the same value. Set `RESOLVE_N8N_WEBHOOK_URL` to the published production Webhook URL displayed by n8n (not the editor URL or test-only URL).
5. Publish the workflow. Create a fresh missing-confirmation case. The operations view must show executor `n8n`, a real n8n execution ID, the action, and a verified acknowledgement.
6. Exercise pending, settlement and dropped-ack cases. Retry an HTTP node in the same execution and confirm the same action/event IDs. A separately started execution is intentionally rejected from taking over an existing run.

Do not set the webhook variable until the imported workflow, credentials and backend URL are ready. An unavailable configured n8n does not masquerade as the local runner: it produces a visible escalation and preserves the trace.

## Hosting review

A Cloud Run deployment needs:

- Built image from `infra/Dockerfile` (port 8000).
- External PostgreSQL, for example an existing Cloud SQL instance. Never use local SQLite on Cloud Run for evidence persistence.
- A service identity with Vertex AI User and Cloud SQL Client (if Cloud SQL is used).
- Secret Manager entries for DATABASE_URL, RESOLVE_IDENTITIES and RESOLVE_WORKFLOW_SECRET.
- `RESOLVE_DEMO_MODE=false`; no public demo identity tokens.
- One worker, max instances 1, concurrency 1, min instances 1 and CPU allocation outside requests for the background simulator/watchdog. This consumes credits while idle; inspect project billing before deploying.
- A public HTTPS entry point protected at the application layer by role and workflow credentials, so n8n Cloud can call it. Public exposure is a separate deployment step.

The repository provides `infra/cloudrun-service.yaml` as a deployment template. Replace the explicit placeholders and review permissions/cost before applying. No cloud resources are created by running the application locally.

The local app remains usable if Vertex fails. Text intake and financial decisions use deterministic fallbacks; the UI says which provider actually supplied intake. Voice dictation depends on browser permissions and support.

## References

- [Vertex quickstart and authentication](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart)
- [Cloud Run background CPU](https://cloud.google.com/blog/topics/developers-practitioners/use-cloud-run-always-cpu-allocation-background-work)
- [Cloud Run to Cloud SQL](https://docs.cloud.google.com/sql/docs/postgres/connect-run)
- [n8n webhook credentials](https://docs.n8n.io/integrations/builtin/credentials/webhook/)
