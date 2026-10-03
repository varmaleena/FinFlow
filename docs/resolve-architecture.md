# Resolve architecture and API

## Components

- `apps/api/app/domain/payment_events.py`: strict event schema; integer paise; deduplication; missing prerequisites; conflicting terminal evidence; deterministic canonical state; versioned policy.
- `apps/api/app/resolve.py`: credential-bound role scope, case intake, persistent workflow stages, idempotent action records, evidence simulator, bounded verifier and timeout watchdog.
- `apps/api/app/services/resolve_ai.py`: Pydantic-validated intake; bounded explanation by selecting only known fact IDs. Invalid model output falls back locally. The mandatory deterministic outcome cannot be removed by the model.
- `apps/api/app/services/gemini_transport.py`: Vertex ADC transport or optional Developer API transport. Secrets remain on the server.
- `apps/web/src/Resolve.tsx`: role views, timeline, Payment Twin, policy, workflow, audit export, voice fallback and safety lab.
- `workflows/n8n/paytm-resolve.json`: real HTTP stages, policy branching, retry configuration, independent-evidence waits, bounded recheck, and escalation.

The existing FINFLOW API and finance dashboard remain available separately. Their shared merchant demo token never authenticates the new `/v2` API. Legacy reset excludes all Resolve records.

## API contracts

All `/v2` routes require a bearer role credential, except workflow callbacks, which require `X-Resolve-Secret`. A frontend role header is never trusted.

| Route | Purpose | Scope |
|---|---|---|
| GET `/v2/session` | Effective role and configured providers | Any role |
| GET `/v2/payments` | Payment references visible to this identity | Owner/merchant filtered |
| GET `/v2/tools/find-transaction` | Exact reference / amount candidate lookup; requires user selection | Owner/merchant filtered |
| POST `/v2/cases` | message, payment_id (optional), language, request_id | Any role, scoped payment |
| POST `/v2/cases/{id}/reference` | Attach missing payment reference to the same case | Case and payment scope checked |
| GET `/v2/cases` and `/v2/cases/{id}` | Role projection of shared cases | Customer owner, merchant tenant, operations |
| GET `/v2/cases/{id}/audit` | Full evidence/action/audit packet | Operations only |
| POST `/v2/cases/{id}/replay` | Return stored action and append replay audit | Operations only |
| POST `/v2/simulator/payments` | Fresh named scenario without deleting history | Operations only |
| POST `/v2/simulator/payments/{id}/events` | Typed synthetic late evidence; request_id deduplication | Operations only |
| POST `/v2/workflow/{id}/{stage}` | run_id + execution_id, ordered stage receipt | Workflow credential only |

Stages: `evidence`, `truth`, `policy`, `action` or `escalate`, `verify1`, `verify2`. Unknown stages fail validation. Out-of-order callbacks return 409. Repeated stage receipts return stored state without executing another side effect. A run is bound to its first n8n execution ID; another execution cannot take it over.

## Persistence and state

Existing SQLAlchemy JSON records store typed payment events, cases, actions and delivery jobs in namespaced record kinds. Record primary keys enforce action identity at the database level. Case request IDs are scoped to the authenticated principal and reject payload changes.

Action idempotency is payment + action + evidence version, so concurrent cases cannot emit two notifications for the same evidence version. A notification acceptance schedules an independent simulator delivery event. Only a later recomputation observing merchant acknowledgement can resolve the notification case. A dropped delivery remains unresolved and escalates.

Payment events include timestamp, source, amount, actor, tool call and idempotency correlation. Derived transition history contains event hashes and evidence references. Monetary totals are integers in paise.

A 120-second deadline catches an n8n webhook that accepts a run but never finishes. Dispatch errors escalate immediately; uncertain delivery is never silently retried through a different engine. Restarting the API resumes persisted local workflow stages and pending simulator deliveries.

## Roles

Customer projections omit policy internals, workflow metadata, actions and audit. Merchant projections add scoped payment evidence and policy. Operations sees the complete operational trace. Workflow secrets cannot access user routes; role tokens cannot invoke workflow stages.

Local demo tokens intentionally let judges switch identities. Hosting must disable demo tokens, install private identity mappings, and provision an external database. This is not an end-user account-management implementation.

## Evidence lifecycle

Canonical states: UNKNOWN, RECEIVED, AUTHORIZED, CAPTURED, MERCHANT_ACK, SETTLEMENT_PENDING, SETTLED, FAILED, EXPIRED, REVERSED, REFUNDED, DISPUTED, CONFLICT. Input list ordering and exact duplicates do not change state/version. Invalid schemas, mismatched payment IDs, conflicting event IDs, mixed amounts and contradictory terminal evidence block autonomous action.

Historical case twins are snapshots at investigation/verification. They are not silently rewritten after a case closes. A later dispute requires a new investigation.
