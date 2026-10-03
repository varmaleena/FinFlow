# Paytm Resolve

An evidence-driven payment resolution workspace with **Customer, Merchant, and AI Operations** views. Gemini on Vertex AI extracts typed complaints; an event-based Payment Twin establishes financial truth; versioned policy gates typed actions; n8n or a durable local runner orchestrates verification and escalation.

All payment data and merchant acknowledgements are synthetic. No Paytm production API and no money movement are used.

## Run

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd ci --prefix apps/web
npm.cmd run build --prefix apps/web
.\.venv\Scripts\python.exe -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The existing finance dashboard remains at `/?view=finance`. Vite/React is retained from the existing project rather than introducing a Next.js migration; all role authorization lives in FastAPI.

For hot reload, run the API and `npm.cmd run dev --prefix apps/web`; Vite proxies both `/v1` and `/v2`.

## Rehearse

1. In **AI Operations**, choose **Missing confirmation**. A fresh synthetic payment is created for every rehearsal. Watch intake → evidence → Payment Twin → policy → notification → independent acknowledgement → verification → resolved.
2. Click **Replay action**. The audit shows the stored action returning without another acknowledgement.
3. Switch to **Customer** or **Merchant**. The same case appears with API-enforced role projections.
4. Choose **Pending payment**. The system refuses finality, makes two bounded checks, and creates a human-review packet.
5. Choose **Settlement investigation**. Capture and settlement are distinguished. Use **Simulate settlement arrival** while monitoring to demonstrate recovery, or let it escalate with missing evidence.
6. Use **Safety lab** for conflicting events, dropped notifications, and a completed settlement.
7. Download **Audit packet** for event hashes, policy version, workflow execution ID, idempotency keys, and verification evidence.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest apps/api/app/tests -q
npm.cmd run build --prefix apps/web
.\.venv\Scripts\python.exe -m scripts.resolve_smoke --url http://127.0.0.1:8000
```

Tests cover 600 generated state scenarios plus authorization, journey, replay, provider failure, workflow ordering, deadlines and verification checks. The smoke test exercises a running server; it writes fresh synthetic cases without deleting history.

## Vertex AI and n8n

The selected GCP project is `spry-catcher-509805-u4`; the selected n8n workspace is `https://varmaleena.app.n8n.cloud`.

Copy `.env.example` to `.env` and configure the relevant values. Vertex uses **Application Default Credentials** locally and an attached service identity on Cloud Run. It does not require a Gemini Developer API key. Keep secrets out of frontend code and Git.

See [integration runbook](docs/resolve-integrations.md), [API and architecture](docs/resolve-architecture.md), [validation report](docs/resolve-validation.md), and the importable [n8n workflow](workflows/n8n/paytm-resolve.json).

## Scope and limitations

- Public, intentionally known role credentials are available only in local demo mode. Private deployment must set `RESOLVE_DEMO_MODE=false` and `RESOLVE_IDENTITIES`.
- The three roles are scoped demo identities, not a production customer onboarding/login system.
- Escalations are stored review packets in the operations queue; no real support ticket or external notification is sent.
- Audit chains are tamper-evident within an exported packet, not externally anchored immutable storage.
- Use one API worker/instance for this hackathon runner. Multi-instance orchestration needs database leases and a durable queue.
- SQLite is durable on this machine, not on an ephemeral Cloud Run filesystem. Cloud hosting must use external PostgreSQL.
- Voice uses browser dictation/speech synthesis when supported and is optional. Text always works.
- A green local run does not prove n8n Cloud or Vertex access. Live integration evidence is recorded separately in the validation report.
