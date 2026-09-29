# API contract

Interactive OpenAPI docs: http://127.0.0.1:8000/docs

All `/v1` endpoints except health require `Authorization: Bearer <DEMO_API_TOKEN>` and scope records by `X-Merchant-Id`. Default merchant is `m_001`. Authentication is for local demonstration only.

- `GET /v1/dashboard`: merchant, transactions, reconciled batches, cashflow, cases.
- `POST /v1/conversations` or `/v1/cases`: `{message, transaction_id?, language?}` creates an investigated case. Ambiguous payment references request clarification.
- `GET /v1/cases/{id}`: persisted case and action status.
- `POST /v1/tools/payment-twin`: `{transaction_id}`.
- `POST /v1/tools/settlement-reconcile`: `{settlement_id}`.
- `GET /v1/tools/cashflow/{merchant_id}`.
- `POST /v1/tools/credit-simulate`: `{principal, months, annual_rate}`.
- `GET /v1/tools/memory?query=...`: non-authoritative playbook retrieval.
- `POST /v1/actions/evaluate`: `{case_id, action_type}` creates an immutable decision.
- `POST /v1/actions/execute`: `{policy_decision_id, idempotency_key}` rechecks policy, executes or dispatches, returns an action. Repeated keys return the same action; reusing a key for a different decision returns 409.
- `POST /v1/actions/{id}/verify`: reads the server-owned verification record; client-supplied success is never accepted.
- `GET /v1/audit/{case_id}`: ordered event trace.
- `POST /v1/demo/reset`: reset selected merchant.
- `POST /v1/voice/transcribe`: multipart `file`.
- `POST /v1/voice/synthesize`: `{message, language}`.
- `POST /internal/actions/{id}/step?step=1`: protected workflow execution with `X-Finflow-Secret`. Numbered monitor steps are retry-idempotent and must arrive in sequence.

Typed financial tools return `{tool, ok, result, trace_id}` with `evidence_ids` in the result. Money amounts in API payloads are INR rupees, computed with Decimal in the financial core and serialized to JSON numbers. Validation errors use 422; missing credentials 401; denied actions 403; inaccessible records 404; stale/reused execution metadata 409; unavailable voice/memory providers 502/503.
