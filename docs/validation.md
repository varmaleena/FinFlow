# Validation record

Validated locally on 29 September 2026 (Asia/Kolkata).

- `python -m pytest apps/api/app/tests -q`: **21 passed**.
- `python -m scripts.run_eval`: **21 passed**, including the six-case golden payment-state dataset.
- `python -m compileall -q apps/api/app scripts`: passed.
- `npm run build`: TypeScript and Vite production build passed. Chart assets are split from the application bundle.
- `npm audit` after upgrading Vite to 6.4.3: **0 vulnerabilities reported**.
- Browser: missing confirmation creates evidence-backed case; notification retry reaches RESOLVED and Outcome verified; transaction updates to Completed.
- Browser: settlement explanation reconciles ₹26,607.75 from visible deductions.
- Browser: credit principal changed from ₹50,000 to ₹51,000; monthly payment recalculated from ₹4,584 to ₹4,675.68.
- Browser: pending payment warns against duplicate collection, monitors two observations and reaches ESCALATED.
- Browser: desktop and mobile breakpoints inspected. A mobile horizontal overflow was fixed and rechecked: document content width equals viewport width.
- Final browser console: no captured errors.
- Demo data reset and API restarted on `127.0.0.1:8000`.

## Unverified external paths

Docker was not installed in the available shell. PostgreSQL/Compose and the imported n8n workflow were not run. Sarvam, LLM and Cognee providers were not live-tested because credentials/services were not supplied. Microphone capture and speech playback require a compatible browser and user permission and were not exercised with live audio. No claim of production readiness or absence of all possible defects is made.
