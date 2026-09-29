# FINFLOW

A local-first merchant finance demo with a React/TypeScript dashboard, FastAPI backend, persistent evidence, deterministic payment state, safe notification retry, pending monitoring, settlement reconciliation and a credit scenario calculator.

## Run on Windows

Requires Python 3.11+ and Node.js 20.19+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
.\scripts\dev.ps1
```

Open **http://127.0.0.1:8000**. The startup script installs locked frontend dependencies when needed, builds the frontend and runs FastAPI. The database seeds itself on first startup. No provider keys or Docker are needed for the local demo.

On macOS/Linux: install requirements, run `npm ci --prefix apps/web`, then `make dev`. For frontend hot reload, run `python -m uvicorn apps.api.app.main:app --reload` and `npm run dev --prefix apps/web` in separate terminals; open port 5173.

## Verify and reset

```powershell
python -m pytest apps/api/app/tests -q
npm.cmd run build --prefix apps/web
python -m scripts.run_eval
python -m scripts.reset_demo
```

The UI also resets the selected merchant. The reset script resets all three merchants and requires the API to be running. `python -m scripts.seed` seeds financial evidence directly; use reset for a clean demo including case history.

## What to try

- Missing confirmation → inspect evidence → retry notification → verified acknowledgement.
- Pending payment → no duplicate collection warning → two periodic rechecks → review escalation.
- Settlement explanation → deterministic gross/refund/fee/adjustment breakdown.
- Cash flow → seeded ledger totals and chart.
- Credit studio → live amount/term/rate controls and transparent amortization.
- Anita Fashions → conflicting payment evidence and unexplained settlement mismatch.
- Case history → reopen any case, view its audit trail, download JSON.

## Optional services

Copy `.env.example` to `.env` for configuration. Sarvam enables recorded audio STT/TTS. Without it, browser dictation and speech synthesis are available where supported; text always works. External API keys never enter frontend code. Changing `DEMO_API_TOKEN` requires entering the matching token in workspace settings.

Docker/PostgreSQL: set `POSTGRES_PASSWORD` and `N8N_ENCRYPTION_KEY` in your environment, then `docker compose -f infra/docker-compose.yml up --build`. Add `--profile workflows` before `up` to start n8n. n8n import and credentials are described in [architecture](docs/architecture.md). Provider-backed Sarvam, Cognee, LLM, n8n and PostgreSQL paths require their own environment and live validation; the no-key SQLite/local workflow path is the reference demo.

See [demo script](docs/demo-script.md), [architecture and limitations](docs/architecture.md), and [API contract](docs/api-contract.md). The app follows the supplied architecture's deterministic safety boundary, with a compact MVP database and explicit local service fallbacks. It is not a production financial system.

The executed checks and external-service limitations are recorded in [validation](docs/validation.md).
