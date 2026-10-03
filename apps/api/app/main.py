import asyncio
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv()
import httpx
from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from .models import Base, engine, Session, Record, Action
from .schemas import Conversation, TransactionInput, SettlementInput, CreditInput, EvaluateInput, ExecuteInput
from .domain.money_twin import reconstruct
from .domain.policy_engine import evaluate
from .domain.financial import reconcile, simulate
from .services.llm import route_intent, generate_response, is_ai_available
from .services.cognee import search_memory
from .resolve import router as resolve_router, seed_resolve, tick as resolve_tick, enrich_outcomes

ROOT = Path(__file__).resolve().parents[3]
WORLD = json.loads((ROOT / 'data/seed/world.json').read_text(encoding='utf-8'))
lock = threading.RLock()
log = logging.getLogger('finflow')
log.setLevel(logging.INFO)
if not log.handlers: log.addHandler(logging.StreamHandler())

def uid(prefix): return prefix + '_' + uuid4().hex[:12]
def now(): return datetime.now(timezone.utc).isoformat()
def put(db, key, kind, merchant, payload):
    record = db.get(Record, key)
    if record: record.payload = dict(payload)
    else: db.add(Record(id=key, kind=kind, merchant_id=merchant, payload=dict(payload)))
    db.flush()
    return payload
def get(db, key, merchant, kind=None):
    record = db.get(Record, key)
    if not record or record.merchant_id != merchant or (kind and record.kind != kind): raise HTTPException(404, 'Record not found')
    return dict(record.payload)
def rows(db, kind, merchant):
    return [dict(r.payload) for r in db.scalars(select(Record).where(Record.kind == kind, Record.merchant_id == merchant))]
def seed(db):
    for tx in WORLD['transactions']: put(db, tx['id'], 'transaction', tx['merchant_id'], tx)
    for batch in WORLD['batches']: put(db, batch['id'], 'settlement', batch['merchant_id'], batch)
    db.commit()
def audit(db, case, event, detail, **extra):
    event_id = uid('evt')
    payload = {'id': event_id, 'case_id': case['id'], 'trace_id': case['trace_id'], 'created_at': now(), 'event_type': event, 'detail': detail, **extra}
    put(db, event_id, 'audit', case['merchant_id'], payload)
    log.info(json.dumps(payload))
def auth(authorization: str | None = Header(default=None), x_merchant_id: str = Header(default='m_001')):
    if os.getenv('RESOLVE_DEMO_MODE', 'true').lower() != 'true' and not os.getenv('DEMO_API_TOKEN'):
        raise HTTPException(403, 'Legacy finance API is disabled without a private credential')
    token = os.getenv('DEMO_API_TOKEN', 'finflow-local-demo')
    if authorization != 'Bearer ' + token: raise HTTPException(401, 'Merchant authentication required')
    if x_merchant_id not in {m['id'] for m in WORLD['merchants']}: raise HTTPException(403, 'Unknown merchant')
    return x_merchant_id
def tool(name, result): return {'tool': name, 'ok': True, 'result': result, 'trace_id': uid('trace')}

def monitor_tick(db, action):
    p = dict(action.payload)
    if p['status'] != 'RUNNING' or p['action_type'] != 'MONITOR_PAYMENT': return p
    case = get(db, action.case_id, action.merchant_id, 'case')
    tx = get(db, case['transaction_id'], action.merchant_id)
    twin = reconstruct(tx)
    p['checks'] = p.get('checks', 0) + 1
    audit(db, case, 'RECHECK', f"Payment recheck {p['checks']}: {twin['derived_state']}", evidence_ids=twin['evidence_ids'])
    if twin['derived_state'] in ('PAID', 'PAID_CONFIRMATION_MISSING', 'FAILED'):
        p.update(status='SUCCEEDED', verified=True, verification=twin['derived_state'])
        case.update(status='RESOLVED', response='Payment finality verified: ' + twin['derived_state'] + '. No new collection was initiated.')
    elif p['checks'] >= 2 or twin['derived_state'] in ('EVIDENCE_CONFLICT', 'MISSING_EVIDENCE'):
        p.update(status='FAILED', verified=False, verification='UNRESOLVED_ESCALATED')
        case.update(status='ESCALATED', response='The payment is still unresolved after rechecking. Do not collect again. A review case with the evidence and action history has been created.')
        audit(db, case, 'ESCALATED', 'Pending payment requires human review.', evidence_ids=twin['evidence_ids'])
    case['action'] = p
    action.payload = p
    put(db, case['id'], 'case', action.merchant_id, case)
    return p

async def worker():
    while True:
        await asyncio.sleep(float(os.getenv('MONITOR_INTERVAL', '15')))
        try:
            with lock, Session() as db:
                for action in db.scalars(select(Action)):
                    if action.payload.get('executor') == 'local': monitor_tick(db, action)
                db.commit()
        except Exception: log.exception('Monitor failed; will retry next interval')

async def resolve_worker():
    while True:
        await asyncio.sleep(1)
        try: await asyncio.to_thread(resolve_tick)
        except Exception: log.exception('Resolve runner failed; persisted work will retry')

async def explanation_worker():
    while True:
        await asyncio.sleep(2)
        try: await asyncio.to_thread(enrich_outcomes)
        except Exception: log.exception('Optional explanation failed; deterministic outcome remains available')

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with Session() as db:
        if not db.get(Record, 'tx_1001'): seed(db)
        seed_resolve(db)
    task = asyncio.create_task(worker())
    resolve_task = asyncio.create_task(resolve_worker())
    explanation_task = asyncio.create_task(explanation_worker())
    yield
    task.cancel()
    resolve_task.cancel()
    explanation_task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    try: await resolve_task
    except asyncio.CancelledError: pass
    try: await explanation_task
    except asyncio.CancelledError: pass

app = FastAPI(title='Paytm Resolve', version='2.0.0', lifespan=lifespan)
app.include_router(resolve_router)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'], allow_methods=['*'], allow_headers=['*'])

@app.get('/v1/health')
def health():
    ai_provider = 'gemini' if os.getenv('GEMINI_API_KEY') else ('openai' if os.getenv('LLM_API_KEY') else 'local')
    return {'status': 'ok', 'mode': 'synthetic demo', 'voice': 'sarvam' if os.getenv('SARVAM_API_KEY') else 'browser', 'executor': 'n8n' if os.getenv('N8N_WEBHOOK_URL') else 'local', 'memory': 'local playbooks', 'ai': ai_provider}

@app.get('/v1/dashboard')
def dashboard(merchant=Depends(auth)):
    with Session() as db:
        txs = rows(db, 'transaction', merchant)
        return {'merchant': next(m for m in WORLD['merchants'] if m['id'] == merchant), 'merchants': WORLD['merchants'], 'transactions': [{**tx, **reconstruct(tx)} for tx in txs], 'settlements': [reconcile(b) for b in rows(db, 'settlement', merchant)], 'cashflow': cashflow(merchant, merchant)['result'], 'cases': rows(db, 'case', merchant)}

@app.post('/v1/tools/payment-twin')
def payment_twin(body: TransactionInput, merchant=Depends(auth)):
    with Session() as db: return tool('get_payment_twin', reconstruct(get(db, body.transaction_id, merchant, 'transaction')))

@app.post('/v1/tools/settlement-reconcile')
def settlement(body: SettlementInput, merchant=Depends(auth)):
    with Session() as db: return tool('reconcile_settlement', reconcile(get(db, body.settlement_id, merchant, 'settlement')))

@app.get('/v1/tools/cashflow/{merchant_id}')
def cashflow(merchant_id: str, merchant=Depends(auth)):
    if merchant_id != merchant: raise HTTPException(403, 'Merchant scope mismatch')
    factor = {'m_001': 1, 'm_002': 1.4, 'm_003': .8}[merchant]
    points = [{**p, 'inflow': round(p['inflow']*factor, 2), 'outflow': round(p['outflow']*factor, 2)} for p in WORLD['cashflow']]
    inflow, outflow = sum(p['inflow'] for p in points), sum(p['outflow'] for p in points)
    return tool('get_cashflow_snapshot', {'points': points, 'inflow': inflow, 'outflow': outflow, 'net': round(inflow-outflow, 2), 'evidence_ids': [merchant + ':ledger:7days'], 'period': '22–28 Sep 2026', 'synthetic': True})

@app.post('/v1/tools/credit-simulate')
def credit(body: CreditInput, merchant=Depends(auth)):
    return tool('simulate_credit', simulate(body.principal, body.months, body.annual_rate))

def classify(message):
    text = message.lower()
    if any(w in text for w in ('refund me', 'reverse payment', 'change bank', 'disburse')): return 'HIGH_RISK'
    if any(w in text for w in ('settlement', 'settle', 'batch', 'सेटल')): return 'SETTLEMENT_EXPLAIN'
    if any(w in text for w in ('borrow', 'loan', 'credit', 'emi', 'लोन', 'उधार')): return 'CREDIT_QUERY'
    if any(w in text for w in ('cash', 'inflow', 'outflow', 'trend', 'कमाई')): return 'CASHFLOW_VIEW'
    if any(w in text for w in ('pay', 'pending', 'notification', 'soundbox', 'tx_', 'पैसे', 'पेमेंट', 'भुगतान')): return 'PAYMENT_ISSUE'
    return 'GENERAL_SUPPORT'

@app.post('/v1/conversations')
@app.post('/v1/cases')
def conversation(body: Conversation, merchant=Depends(auth)):
    with lock, Session() as db:
        intent = route_intent(body.message, classify(body.message))
        merchant_info = next(m for m in WORLD['merchants'] if m['id'] == merchant)
        case = {'id': uid('case'), 'merchant_id': merchant, 'trace_id': uid('trace'), 'intent': intent, 'status': 'INVESTIGATING', 'message': body.message, 'created_at': now(), 'language': body.language, 'evidence': [], 'response': '', 'ai_powered': is_ai_available()}
        audit(db, case, 'RECEIVED', 'Merchant request received.')
        ai_context = {}
        if intent == 'PAYMENT_ISSUE':
            txid = body.transaction_id
            match = re.search(r'tx_\d+', body.message)
            if match: txid = match.group()
            if not txid:
                candidates = rows(db, 'transaction', merchant)
                amounts = re.findall(r'\d[\d,]*(?:\.\d+)?', body.message)
                if amounts: candidates = [t for t in candidates if t['amount'] in [float(a.replace(',', '')) for a in amounts]]
                if len(candidates) == 1: txid = candidates[0]['id']
            if not txid:
                case.update(status='OPEN', response='Please select a transaction or share its transaction ID so I can check the correct payment.')
            else:
                tx = get(db, txid, merchant, 'transaction')
                twin = reconstruct(tx)
                case.update(transaction_id=txid, twin=twin, root_cause=twin['derived_state'], evidence=[{'id': eid, 'source': label, 'value': value, 'payload_hash': hashlib.sha256(str(value).encode()).hexdigest()} for eid, label, value in zip(twin['evidence_ids'], ['Customer debit', 'Payment rail', 'Paytm ledger', 'Merchant confirmation'], [twin['customer_debit'], twin['rail'], twin['provider'], twin['confirmation']])])
                audit(db, case, 'INVESTIGATED', twin['derived_state'], evidence_ids=twin['evidence_ids'])
                action = 'RETRY_MERCHANT_NOTIFICATION' if twin['derived_state'] == 'PAID_CONFIRMATION_MISSING' else 'MONITOR_PAYMENT'
                policy = {**evaluate(twin['derived_state'], action, tx['attempts']), 'id': uid('pol'), 'case_id': case['id'], 'transaction_id': txid}
                case['policy'] = policy
                put(db, policy['id'], 'policy', merchant, policy)
                audit(db, case, 'POLICY', policy['reason'], policy_decision_id=policy['id'])
                if policy['allowed']:
                    case.update(status='OPEN', response=(f"The ₹{tx['amount']:,.2f} payment succeeded on both the payment rail and Paytm ledger. Only the merchant confirmation is missing. I can safely retry the notification." if action == 'RETRY_MERCHANT_NOTIFICATION' else f"The ₹{tx['amount']:,.2f} payment is pending after debit. Do not collect again. I can monitor it and escalate if it remains unresolved."))
                elif twin['derived_state'] == 'PAID': case.update(status='RESOLVED', response='Payment success and merchant acknowledgement are verified. No action is needed.')
                else:
                    case.update(status='ESCALATED', response='This payment needs human review. ' + policy['reason'] + ' Do not collect again while its status is unresolved.')
                    audit(db, case, 'ESCALATED', case['response'], evidence_ids=twin['evidence_ids'])
                ai_context = {'transaction_id': txid, 'amount': tx['amount'], 'payment_state': twin['derived_state'], 'customer_debit': twin['customer_debit'], 'rail_status': twin['rail'], 'paytm_status': twin['provider'], 'confirmation': twin['confirmation'], 'policy_allowed': policy['allowed'], 'policy_reason': policy['reason'], 'next_action': action if policy['allowed'] else None}
        elif intent == 'SETTLEMENT_EXPLAIN':
            result = reconcile(rows(db, 'settlement', merchant)[0])
            case.update(result=result, status='RESOLVED' if result['derived_state'] == 'RECONCILED' else 'ESCALATED', root_cause=result['derived_state'], evidence=[{'id': x, 'source': x.split(':')[-1], 'value': result[x.split(':')[-1]]} for x in result['evidence_ids']], response=f"Gross ₹{result['gross']:,.2f} less refunds ₹{result['refunds']:,.2f}, fees ₹{result['fees']:,.2f} and adjustments ₹{result['adjustments']:,.2f} gives ₹{result['expected_net']:,.2f}. " + ('This matches the credited batch.' if result['difference'] == 0 else f"The credited amount differs by ₹{result['difference']:,.2f}; review is required."))
            ai_context = {'settlement': result}
        elif intent == 'CASHFLOW_VIEW':
            result = cashflow(merchant, merchant)['result']
            case.update(result=result, status='RESOLVED', response=f"For {result['period']}, inflow is ₹{result['inflow']:,.2f} and outflow is ₹{result['outflow']:,.2f}. Net cash flow is ₹{result['net']:,.2f}. These are synthetic demo ledger totals.", evidence=[{'id': result['evidence_ids'][0], 'source': 'Demo ledger', 'value': result['period']}])
            ai_context = {'cashflow': result}
        elif intent == 'CREDIT_QUERY':
            match = re.search(r'\d[\d,]*', body.message)
            principal = float(match.group().replace(',', '')) if match else 50000
            if not 1000 <= principal <= 1000000: raise HTTPException(422, 'Use an illustrative principal between ₹1,000 and ₹10,00,000.')
            result = simulate(principal, 12, 18)
            case.update(result=result, status='RESOLVED', response=f"At an illustrative 18% annual rate over 12 months, ₹{principal:,.0f} means approximately ₹{result['monthly_payment']:,.2f} per month. {result['assumptions']}", evidence=[{'id': 'credit_assumptions_v1', 'source': 'Scenario assumptions', 'value': '18% APR · 12 months · no fees'}])
            ai_context = {'credit_simulation': result, 'principal_requested': principal}
        else:
            case.update(status='ESCALATED' if intent == 'HIGH_RISK' else 'OPEN', response='Refunds, reversals, bank changes and disbursals require human review and cannot be executed here.' if intent == 'HIGH_RISK' else 'I can check payments, explain settlements, summarize cash flow, or calculate a borrowing scenario. Select a transaction to begin.')
        # Hindi template override for payment states (deterministic safety boundary)
        if body.language == 'hi-IN' and intent == 'PAYMENT_ISSUE' and case.get('twin'):
            state = case['twin']['derived_state']
            case['response'] = {'PAID_CONFIRMATION_MISSING': 'भुगतान सफल है। केवल व्यापारी की पुष्टि नहीं मिली है। सूचना दोबारा भेज सकते हैं।', 'PENDING_FINALITY': 'पैसे कट गए हैं, लेकिन भुगतान अभी लंबित है। दोबारा भुगतान न लें। स्थिति की निगरानी कर सकते हैं।', 'PAID': 'भुगतान और व्यापारी की पुष्टि सत्यापित हैं।'}.get(state, 'इस भुगतान की मानव समीक्षा आवश्यक है। दोबारा भुगतान न लें।')
        # AI response enrichment — non-payment intents and general queries get AI-generated text
        # Payment issues keep deterministic response for safety, AI only enhances non-safety paths
        if ai_context and intent not in ('HIGH_RISK',) and is_ai_available():
            try:
                ai_resp = generate_response(
                    merchant_name=merchant_info['name'],
                    merchant_segment=merchant_info.get('segment', ''),
                    intent=intent,
                    message=body.message,
                    context=ai_context,
                    language=body.language,
                )
                if ai_resp:
                    # For non-payment intents, replace with richer AI response
                    # For payment issues, prepend AI context to deterministic safety response
                    if intent == 'PAYMENT_ISSUE':
                        case['ai_insight'] = ai_resp
                    else:
                        case['response'] = ai_resp
            except Exception:
                pass  # AI failure never blocks deterministic response
        audit(db, case, 'EXPLAINED', case['response'], evidence_ids=[e['id'] for e in case['evidence']])
        put(db, case['id'], 'case', merchant, case)
        db.commit()
        return case

@app.get('/v1/cases/{case_id}')
def case_detail(case_id: str, merchant=Depends(auth)):
    with Session() as db: return get(db, case_id, merchant, 'case')

@app.get('/v1/audit/{case_id}')
def case_audit(case_id: str, merchant=Depends(auth)):
    with Session() as db:
        get(db, case_id, merchant, 'case')
        return sorted([a for a in rows(db, 'audit', merchant) if a['case_id'] == case_id], key=lambda a: a['created_at'])

@app.post('/v1/actions/evaluate')
def evaluate_action(body: EvaluateInput, merchant=Depends(auth)):
    with lock, Session() as db:
        case = get(db, body.case_id, merchant, 'case')
        tx = get(db, case.get('transaction_id', ''), merchant, 'transaction')
        policy = {**evaluate(reconstruct(tx)['derived_state'], body.action_type, tx['attempts']), 'id': uid('pol'), 'case_id': case['id'], 'transaction_id': tx['id']}
        put(db, policy['id'], 'policy', merchant, policy)
        audit(db, case, 'POLICY', policy['reason'], policy_decision_id=policy['id'])
        db.commit()
        return policy

def run_notification(db, action):
    p = dict(action.payload)
    if p['status'] != 'RUNNING': return p
    case = get(db, action.case_id, action.merchant_id, 'case')
    tx = get(db, case['transaction_id'], action.merchant_id, 'transaction')
    allowed = evaluate(reconstruct(tx)['derived_state'], p['action_type'], tx['attempts'])
    if not allowed['allowed']:
        p.update(status='FAILED', verified=False, verification='POLICY_CHANGED')
        action.payload = p
        case.update(status='ESCALATED', action=p, response='Evidence changed before execution. No notification was sent; human review is required.')
        put(db, case['id'], 'case', action.merchant_id, case)
        audit(db, case, 'ESCALATED', 'Policy changed before workflow execution', action_id=action.id)
        return p
    tx.update(confirmation='ACKNOWLEDGED', attempts=tx['attempts'] + 1)
    put(db, tx['id'], 'transaction', action.merchant_id, tx)
    verified = reconstruct(tx)['derived_state'] == 'PAID'
    p.update(status='SUCCEEDED' if verified else 'FAILED', verified=verified, verification=tx['confirmation'])
    action.payload = p
    case.update(status='RESOLVED' if verified else 'ESCALATED', action=p, twin=reconstruct(tx), response='Merchant acknowledgement verified. The notification was delivered successfully. No money was moved.')
    if case.get('language') == 'hi-IN': case['response'] = 'व्यापारी की पुष्टि सत्यापित है। सूचना सफलतापूर्वक पहुँच गई है। कोई पैसा नहीं भेजा गया।'
    put(db, case['id'], 'case', action.merchant_id, case)
    audit(db, case, 'VERIFIED', p['verification'], action_id=action.id, policy_decision_id=p['policy_decision_id'], idempotency_key=action.idempotency_key, workflow_id=p['workflow_id'])
    return p

@app.post('/v1/actions/execute')
def execute(body: ExecuteInput, merchant=Depends(auth)):
    with lock, Session() as db:
        existing = db.scalar(select(Action).where(Action.merchant_id == merchant, Action.idempotency_key == body.idempotency_key))
        if existing:
            if existing.payload['policy_decision_id'] != body.policy_decision_id: raise HTTPException(409, 'Idempotency key belongs to another decision')
            return existing.payload
        policy = get(db, body.policy_decision_id, merchant, 'policy')
        case = get(db, policy['case_id'], merchant, 'case')
        tx = get(db, policy['transaction_id'], merchant, 'transaction')
        fresh = evaluate(reconstruct(tx)['derived_state'], policy['action_type'], tx['attempts'])
        if not policy['allowed'] or not fresh['allowed']: raise HTTPException(403, 'Action denied by current policy')
        prior = db.scalar(select(Action).where(Action.case_id == case['id']))
        if prior: return prior.payload
        action_id = uid('act')
        executor = 'n8n' if os.getenv('N8N_WEBHOOK_URL') else 'local'
        p = {'id': action_id, 'case_id': case['id'], 'action_type': policy['action_type'], 'policy_decision_id': policy['id'], 'idempotency_key': body.idempotency_key, 'status': 'RUNNING', 'verified': False, 'executor': executor, 'workflow_id': executor + ':' + action_id, 'checks': 0}
        action = Action(id=action_id, merchant_id=merchant, case_id=case['id'], idempotency_key=body.idempotency_key, payload=p)
        db.add(action)
        case.update(status='WAITING', action=p)
        put(db, case['id'], 'case', merchant, case)
        audit(db, case, 'ACTION_STARTED', policy['action_type'], action_id=action_id, policy_decision_id=policy['id'], idempotency_key=body.idempotency_key, workflow_id=p['workflow_id'])
        if executor == 'local' and p['action_type'] == 'RETRY_MERCHANT_NOTIFICATION': p = run_notification(db, action)
        db.commit()
    if executor == 'n8n':
        try:
            response = httpx.post(os.environ['N8N_WEBHOOK_URL'], json={'action_id': action_id, 'merchant_id': merchant, 'action_type': p['action_type']}, headers={'X-Finflow-Secret': os.getenv('WORKFLOW_SECRET', '')}, timeout=15)
            response.raise_for_status()
        except httpx.HTTPError:
            with lock, Session() as db:
                action = db.get(Action, action_id)
                if action.payload['status'] != 'RUNNING': return action.payload
                p = {**action.payload, 'status': 'FAILED', 'verification': 'WORKFLOW_DISPATCH_FAILED'}
                action.payload = p
                case = get(db, action.case_id, merchant)
                case.update(status='ESCALATED', action=p, response='Workflow dispatch failed. Human review is required; no success is claimed.')
                put(db, case['id'], 'case', merchant, case)
                audit(db, case, 'ESCALATED', 'Workflow dispatch failed')
                db.commit()
    return p

@app.post('/v1/actions/{action_id}/verify')
def verify(action_id: str, merchant=Depends(auth)):
    with Session() as db:
        action = db.get(Action, action_id)
        if not action or action.merchant_id != merchant: raise HTTPException(404, 'Action not found')
        return action.payload

@app.post('/internal/actions/{action_id}/step')
def workflow_step(action_id: str, step: int = 0, x_finflow_secret: str | None = Header(default=None)):
    secret = os.getenv('WORKFLOW_SECRET')
    if not secret or x_finflow_secret != secret: raise HTTPException(403, 'Workflow authentication required')
    with lock, Session() as db:
        action = db.get(Action, action_id)
        if not action: raise HTTPException(404, 'Action not found')
        if step and action.payload.get('checks', 0) >= step: return action.payload
        if step and step != action.payload.get('checks', 0) + 1: raise HTTPException(409, 'Workflow step out of order')
        result = run_notification(db, action) if action.payload['action_type'] == 'RETRY_MERCHANT_NOTIFICATION' else monitor_tick(db, action)
        db.commit()
        return result

@app.post('/v1/demo/reset')
def reset(merchant=Depends(auth)):
    with lock, Session() as db:
        db.execute(delete(Action).where(Action.merchant_id == merchant))
        db.execute(delete(Record).where(Record.merchant_id == merchant, ~Record.kind.like('resolve_%')))
        for tx in WORLD['transactions']:
            if tx['merchant_id'] == merchant: put(db, tx['id'], 'transaction', merchant, tx)
        for batch in WORLD['batches']:
            if batch['merchant_id'] == merchant: put(db, batch['id'], 'settlement', merchant, batch)
        db.commit()
    return {'ok': True}

@app.get('/v1/tools/memory')
async def memory(query: str, merchant=Depends(auth)):
    try: return tool('policy_search', await search_memory(query))
    except Exception: raise HTTPException(503, 'Memory retrieval unavailable; canonical financial tools remain available')

@app.post('/v1/voice/transcribe')
async def transcribe(file: UploadFile = File(...), merchant=Depends(auth)):
    key = os.getenv('SARVAM_API_KEY')
    if not key: raise HTTPException(503, 'Sarvam is not configured. Use browser dictation or type your message.')
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024: raise HTTPException(413, 'Audio must be under 5 MB')
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post('https://api.sarvam.ai/speech-to-text', headers={'api-subscription-key': key}, files={'file': (file.filename or 'clip.webm', data, file.content_type)}, data={'model': os.getenv('SARVAM_STT_MODEL', 'saaras:v3'), 'mode': 'transcribe'})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError: raise HTTPException(502, 'Speech provider unavailable; please type your message.')

@app.post('/v1/voice/synthesize')
async def synthesize(body: Conversation, merchant=Depends(auth)):
    key = os.getenv('SARVAM_API_KEY')
    if not key: raise HTTPException(503, 'Sarvam is not configured')
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post('https://api.sarvam.ai/text-to-speech', headers={'api-subscription-key': key}, json={'text': body.message, 'target_language_code': body.language, 'model': 'bulbul:v3', 'speaker': os.getenv('SARVAM_SPEAKER', 'shubh')})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError: raise HTTPException(502, 'Speech provider unavailable')

dist = ROOT / 'apps/web/dist'
if dist.exists(): app.mount('/', StaticFiles(directory=dist, html=True), name='web')
