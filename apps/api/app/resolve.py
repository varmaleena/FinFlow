"""Paytm Resolve API: credential-bound roles, durable workflow stages and event verification."""
import hmac
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import Literal
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, text
from .models import Session, Record
from .domain.payment_events import SCENARIOS, event, twin, decide, digest, POLICY_VERSION
from .services.resolve_ai import extract, explain, grounded_explanation
from .services.gemini_transport import configured as ai_configured, provider as ai_provider
from .services.resolve_memory import search_context

router = APIRouter(prefix='/v2')
lock = threading.RLock()

@contextmanager
def transaction():
    """Serialize workflow mutations across API processes as well as worker threads."""
    with Session() as db:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text("SELECT pg_advisory_xact_lock(741932016)"))
        elif db.bind.dialect.name == 'sqlite':
            db.execute(text('BEGIN IMMEDIATE'))
        yield db
def now(): return datetime.now(timezone.utc)
def iso(): return now().isoformat()
def uid(prefix): return prefix + '_' + uuid4().hex[:16]
def save(db, kind, value):
    old = db.get(Record, value['id'])
    if old: old.payload = json.loads(json.dumps(value))
    else: db.add(Record(id=value['id'], kind='resolve_'+kind, merchant_id=value.get('merchant_id', 'm_001'), payload=json.loads(json.dumps(value))))
    db.flush()
def get(db, key, kind):
    row = db.get(Record, key)
    if not row or row.kind != 'resolve_'+kind: raise HTTPException(404, 'Record not found')
    return json.loads(json.dumps(row.payload))
def all_rows(db, kind): return [r.payload for r in db.scalars(select(Record).where(Record.kind == 'resolve_'+kind))]

def principals():
    configured = os.getenv('RESOLVE_IDENTITIES')
    if configured: return json.loads(configured)
    if os.getenv('RESOLVE_DEMO_MODE', 'true').lower() != 'true': return {}
    return {'resolve-customer-demo': {'role': 'customer', 'customer_id': 'customer_001', 'merchant_id': 'm_001'},
            'resolve-merchant-demo': {'role': 'merchant', 'merchant_id': 'm_001'},
            'resolve-operations-demo': {'role': 'operations', 'merchant_id': 'm_001'}}

def identity(authorization: str = Header(default='')):
    token = authorization.removeprefix('Bearer ')
    for secret, principal in principals().items():
        if hmac.compare_digest(token, secret): return principal
    raise HTTPException(401, 'A valid role credential is required')
def workflow_auth(x_resolve_secret: str = Header(default='')):
    secret = os.getenv('RESOLVE_WORKFLOW_SECRET', '')
    if not secret or not hmac.compare_digest(secret, x_resolve_secret): raise HTTPException(403, 'Workflow credential required')
def operations(p=Depends(identity)):
    if p['role'] != 'operations': raise HTTPException(403, 'Operations role required')
    return p
def visible(value, p):
    return p['role'] == 'operations' or (value['merchant_id'] == p.get('merchant_id') and (p['role'] != 'customer' or value['customer_id'] == p.get('customer_id')))
def scoped(db, key, kind, p):
    value = get(db, key, kind)
    if not visible(value, p): raise HTTPException(404, 'Record not found')
    return value

def append_audit(case, stage, detail, **extra):
    previous = case['audit'][-1]['hash'] if case['audit'] else '0'*64
    entry = {'id': uid('audit'), 'timestamp': iso(), 'actor': extra.pop('actor', 'resolve-system'), 'component': stage,
        'detail': detail, 'case_id': case['id'], 'policy_version': POLICY_VERSION, 'previous_hash': previous, **extra}
    entry.setdefault('input_ref', case['audit'][-1]['output_ref'] if case['audit'] and 'output_ref' in case['audit'][-1] else case['id'])
    entry.setdefault('output_ref', entry['id'])
    entry.setdefault('status', 'COMPLETE')
    entry['hash'] = digest(entry)
    case['audit'].append(entry)

def outcome(c, status, reason):
    c['status'] = status
    history = c.setdefault('outcome_history', [])
    if not history or history[-1]['status'] != status:
        history.append({'status': status, 'timestamp': iso(), 'reason': reason})
        append_audit(c, 'OUTCOME', reason, status=status)

def seed_resolve(db):
    if db.get(Record, 'pay_confirmation'): return
    for name, (label, kinds) in SCENARIOS.items():
        payment_id = 'pay_'+name
        save(db, 'payment', {'id': payment_id, 'merchant_id': 'm_001', 'customer_id': 'customer_001', 'label': label,
            'scenario': name, 'events': [event(payment_id, k, index=i) for i, k in enumerate(kinds)]})
    # A second identity's evidence exists to make authorization tests meaningful.
    save(db, 'payment', {'id': 'pay_private', 'merchant_id': 'm_002', 'customer_id': 'customer_002', 'label': 'Private merchant payment', 'scenario': 'pending', 'events': [event('pay_private', 'RECEIVED')]})
    db.commit()

def projection(case, p):
    if p['role'] == 'operations': return case
    fields = ('id', 'payment_id', 'merchant_id', 'customer_id', 'message', 'intent', 'status', 'created_at', 'updated_at', 'response', 'language', 'severity', 'verification', 'explanation', 'outcome_history')
    result = {k: case[k] for k in fields if k in case}
    if p['role'] == 'merchant': result.update(twin=case.get('twin'), policy=case.get('policy'))
    elif case.get('twin'): result['payment_state'] = case['twin']['canonical_state']
    return result

class NewCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=3, max_length=2000)
    payment_id: str | None = None
    language: Literal['en-IN', 'hi-IN'] = 'en-IN'
    request_id: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9:_-]+$')

class ReferenceInput(BaseModel):
    payment_id: str = Field(min_length=4, max_length=150)

@router.post('/cases/{case_id}/reference')
def attach_reference(case_id: str, body: ReferenceInput, p=Depends(identity)):
    with lock, transaction() as db:
        c = scoped(db, case_id, 'case', p)
        payment = scoped(db, body.payment_id, 'payment', p)
        if c.get('payment_id'):
            if c['payment_id'] != body.payment_id: raise HTTPException(409, 'The case already has a different verified reference')
            return projection(c, p)
        c.update(payment_id=payment['id'], merchant_id=payment['merchant_id'], customer_id=payment['customer_id'])
        append_audit(c, 'REFERENCE', 'User selected a scoped payment reference.', actor=p['role'])
        enqueue(db, c); db.commit()
    if c['workflow']['executor'] == 'n8n': dispatch(c)
    with Session() as db: return projection(get(db, case_id, 'case'), p)

@router.get('/tools/find-transaction')
def find_transaction(reference: str | None = None, amount_paise: int | None = None, p=Depends(identity)):
    with Session() as db:
        values = [v for v in all_rows(db, 'payment') if visible(v, p)]
        if reference: values = [v for v in values if v['id'] == reference]
        if amount_paise is not None: values = [v for v in values if twin(v['id'], v['events'])['amount_paise'] == amount_paise]
        return {'candidates': [{'payment_id':v['id'],'label':v['label']} for v in values], 'requires_user_selection': True}

@router.get('/session')
def session(p=Depends(identity)):
    return {**p, 'demo': not bool(os.getenv('RESOLVE_IDENTITIES')), 'executor': 'n8n' if os.getenv('RESOLVE_N8N_WEBHOOK_URL') else 'local', 'ai_configured': ai_configured(), 'ai_provider': ai_provider()}

@router.get('/payments')
def payments(p=Depends(identity)):
    with Session() as db:
        return [{'id': v['id'], 'label': v['label'], 'amount_paise': twin(v['id'], v['events'])['amount_paise'], 'state': twin(v['id'], v['events'])['canonical_state']} for v in all_rows(db, 'payment') if visible(v, p)]

@router.get('/cases')
def cases(p=Depends(identity)):
    with Session() as db: return [projection(c, p) for c in sorted(all_rows(db, 'case'), key=lambda c: c['created_at'], reverse=True) if visible(c, p)]

@router.get('/cases/{case_id}')
def detail(case_id: str, p=Depends(identity)):
    with Session() as db: return projection(scoped(db, case_id, 'case', p), p)

@router.get('/cases/{case_id}/audit')
def audit_export(case_id: str, p=Depends(operations)):
    with Session() as db:
        c = scoped(db, case_id, 'case', p)
        return {'case': c, 'audit': c['audit'], 'events': get(db, c['payment_id'], 'payment')['events'] if c.get('payment_id') else [], 'hash_chain': 'sha256; tamper-evident within exported chain, not externally anchored'}

@router.get('/cases/{case_id}/context')
def contextual_memory(case_id: str, p=Depends(identity)):
    if p['role'] == 'customer': raise HTTPException(403, 'Support context is available to merchant and operations roles')
    with Session() as db:
        c = scoped(db, case_id, 'case', p)
        related = [{'case_id':v['id'],'resolution_note':v['response'],'status':v['status']} for v in all_rows(db, 'case')
            if v['id'] != c['id'] and v['merchant_id'] == c['merchant_id'] and v['intent']==c['intent'] and v['status']=='RESOLVED' and visible(v,p)][-3:]
    return {**search_context(c['intent'],c.get('twin',{}).get('canonical_state','UNKNOWN')),'related_cases':related}

def enqueue(db, c):
    c['workflow'] = {'id': uid('run'), 'executor': 'n8n' if os.getenv('RESOLVE_N8N_WEBHOOK_URL') else 'local',
        'n8n_execution_id': None, 'status': 'QUEUED', 'stage': 'evidence', 'started_at': iso(), 'checks': 0,
        'next_at': iso(), 'deadline': (now()+timedelta(seconds=120)).isoformat(), 'receipts': {}}
    c['status'] = 'INVESTIGATING'
    append_audit(c, 'WORKFLOW', 'Resolution workflow queued', workflow_id=c['workflow']['id'])
    save(db, 'case', c)

@router.post('/cases')
def create(body: NewCase, p=Depends(identity)):
    # Network-dependent extraction occurs outside the database transaction / workflow lock.
    intake, ai = extract(body.message)
    with lock, transaction() as db:
        fingerprint = digest({'body': body.model_dump(), 'principal': p})
        case_id = 'resolve_case_'+digest({'request': body.request_id, 'principal': p})[:24]
        existing = db.get(Record, case_id)
        if existing:
            if existing.payload['request_hash'] != fingerprint: raise HTTPException(409, 'Request ID was already used with different input')
            return projection(existing.payload, p)
        payment_id = body.payment_id or intake.payment_id
        payment = scoped(db, payment_id, 'payment', p) if payment_id else None
        c = {'id': case_id, 'merchant_id': payment['merchant_id'] if payment else p.get('merchant_id', 'm_001'),
            'customer_id': payment['customer_id'] if payment else p.get('customer_id', 'customer_001'),
            'role': p['role'], 'message': body.message, 'payment_id': payment_id, 'request_hash': fingerprint,
            'created_at': iso(), 'updated_at': iso(), 'status': 'NEEDS_INPUT', 'severity': 'normal',
            'language': body.language, 'intent': intake.intent, 'intake': intake.model_dump(), 'ai': ai,
            'audit': [], 'actions': [], 'response': 'Select a payment or provide its reference. A suggested match is never treated as verified evidence.'}
        append_audit(c, 'COMPLAINT', 'Complaint received.', actor=p['role'])
        append_audit(c, 'INTAKE', 'Complaint validated; financial claims remain unverified.', model=ai['model'], provider=ai['provider'], extraction=intake.model_dump())
        if payment: enqueue(db, c)
        else: save(db, 'case', c)
        db.commit()
    if payment and c['workflow']['executor'] == 'n8n': dispatch(c)
    with Session() as db: return projection(get(db, case_id, 'case'), p)

def dispatch(c):
    try:
        if not os.getenv('RESOLVE_WORKFLOW_SECRET'): raise ValueError('Workflow secret missing')
        r = httpx.post(os.environ['RESOLVE_N8N_WEBHOOK_URL'], json={'case_id': c['id'], 'run_id': c['workflow']['id']},
            headers={'X-Resolve-Secret': os.environ['RESOLVE_WORKFLOW_SECRET']}, timeout=10)
        r.raise_for_status()
    except (httpx.HTTPError, ValueError):
        with lock, transaction() as db:
            current = get(db, c['id'], 'case')
            if current['status'] in ('RESOLVED', 'ESCALATED'): return
            escalate(db, current, 'n8n dispatch failed or timed out; no success is claimed.')
            db.commit()

def escalate(db, c, reason):
    outcome(c, 'ESCALATED', reason); c['severity'] = 'high'
    c['workflow'].update(status='ESCALATED', ended_at=iso(), stage='done')
    key = c['id']+':create_escalation'
    a = {'id': 'resolve_action_'+digest(key)[:24], 'merchant_id': c['merchant_id'], 'tool_name': 'create_escalation', 'actor': 'resolve-system', 'idempotency_key': key, 'policy_version': POLICY_VERSION, 'created_at': iso(), 'result': 'REVIEW_PACKET_CREATED'}
    if not any(v['id'] == a['id'] for v in c['actions']):
        save(db, 'action', a); c['actions'].append(a)
    c['escalation'] = {'id': uid('esc'), 'status': 'OPEN', 'reason': reason, 'evidence_refs': c.get('twin', {}).get('evidence_refs', []), 'policy': c.get('policy'), 'actions': c['actions'][:], 'workflow_id': c['workflow']['id']}
    c['response'] = explain(c.get('twin', {'canonical_state': 'UNKNOWN'}), c['status'], c['intent'], c['language'])
    append_audit(c, 'ESCALATION', reason, evidence_refs=c['escalation']['evidence_refs'])
    c['updated_at'] = iso(); save(db, 'case', c)

def advance(db, c, stage, execution_id=None):
    w = c['workflow']
    if c['status'] in ('RESOLVED', 'ESCALATED'): return c
    if execution_id:
        if w['n8n_execution_id'] and w['n8n_execution_id'] != execution_id: raise HTTPException(409, 'Run belongs to a different n8n execution')
        if not w['n8n_execution_id']:
            append_audit(c, 'ORCHESTRATION', 'n8n execution bound to this case and run.', actor='n8n', execution_id=execution_id, workflow_id=w['id'])
        w['n8n_execution_id'] = execution_id
    # Each stage receipt makes duplicate webhook deliveries side-effect free.
    if stage in w['receipts']: return c
    if stage != w['stage']: raise HTTPException(409, 'Workflow stage out of order; expected '+w['stage'])
    payment = get(db, c['payment_id'], 'payment')
    w['status'] = 'RUNNING'
    t = twin(payment['id'], payment['events'])
    if stage == 'evidence':
        c['twin'] = t
        append_audit(c, 'EVIDENCE', f"Validated {len(t['evidence_refs'])} payment events.", evidence_refs=t['evidence_refs'])
        w['stage'] = 'truth'
    elif stage == 'truth':
        c['twin'] = t
        append_audit(c, 'PAYMENT_TWIN', t['canonical_state'], twin_version=t['version'])
        w['stage'] = 'policy'
    elif stage == 'policy':
        c['policy'] = {**decide(t, c['intent']), 'id': uid('decision'), 'evaluated_at': iso(), 'canonical_state': t['canonical_state'], 'evidence_refs': t['evidence_refs']}
        append_audit(c, 'POLICY', c['policy']['reason'], decision_id=c['policy']['id'])
        w['stage'] = 'escalate' if c['policy']['outcome'] == 'ESCALATE' else 'action'
        if w['stage'] == 'escalate': outcome(c, 'BLOCKED', c['policy']['reason'])
    elif stage == 'escalate':
        escalate(db, c, c['policy']['reason'])
    elif stage == 'action':
        fresh = decide(t, c['intent'])
        if fresh['action'] != c['policy']['action'] or fresh['outcome'] == 'ESCALATE':
            c['twin'] = t; escalate(db, c, 'Evidence changed before action; policy no longer permits the planned action.'); return c
        name = fresh['action']; key = f"{payment['id']}:{name}:{t['version']}"
        # Persistent, payment-scoped key deduplicates across cases, not only within a run.
        action_id = 'resolve_action_'+digest(key)[:24]
        prior = db.get(Record, action_id)
        if prior: action = prior.payload
        else:
            action = {'id': action_id, 'merchant_id': c['merchant_id'], 'tool_name': name, 'actor': w['executor'], 'idempotency_key': key, 'policy_version': POLICY_VERSION, 'created_at': iso(), 'result': 'ACCEPTED_NOT_VERIFIED'}
            if name == 'retry_merchant_notification':
                # Simulator schedules independent evidence; acceptance itself cannot resolve a case.
                due = {'id': 'delivery_'+digest(key)[:24], 'merchant_id': c['merchant_id'], 'payment_id': payment['id'], 'action_id': action_id, 'key': key,
                    'due_at': (now()+timedelta(seconds=2)).isoformat(), 'delivered': False, 'drop': payment['scenario'] == 'notification_failure'}
                save(db, 'delivery', due)
            save(db, 'action', action)
        c['actions'].append(action)
        append_audit(c, 'ACTION', name, action_id=action_id, idempotency_key=key, duplicate=bool(prior), decision_id=c['policy']['id'], actor=w['executor'])
        if name == 'schedule_status_check': outcome(c, 'MONITORING', c['policy']['reason'])
        else: c['status'] = 'WAITING'
        w['stage'] = 'verify1'
        w['next_at'] = (now()+timedelta(seconds=3)).isoformat()
    elif stage in ('verify1', 'verify2'):
        if now() < datetime.fromisoformat(w['next_at']): raise HTTPException(409, 'Verification wait has not elapsed')
        c['twin'] = t
        expected = ('SETTLED' if c['intent'] == 'SETTLEMENT' else t['canonical_state'] if c['policy']['action'] == 'explain_resolution' else 'PAYMENT_FINALITY' if c['policy']['action'] == 'schedule_status_check' else 'MERCHANT_ACK')
        if c['policy']['action'] == 'explain_resolution': verified = t['final'] or (t['canonical_state'] == 'SETTLEMENT_PENDING' and t['merchant_ack'])
        elif c['intent'] == 'SETTLEMENT': verified = t['canonical_state'] == 'SETTLED'
        elif c['policy']['action'] == 'retry_merchant_notification': verified = t['merchant_ack'] and t['canonical_state'] not in ('UNKNOWN', 'CONFLICT', 'DISPUTED', 'REVERSED', 'REFUNDED')
        else: verified = t['final'] and t['canonical_state'] not in ('DISPUTED', 'UNKNOWN', 'CONFLICT')
        w['checks'] += 1
        c['verification'] = {'verified': verified, 'expected': expected, 'observed': t['canonical_state'], 'evidence_refs': t['evidence_refs'], 'checked_at': iso(), 'twin_version': t['version']}
        append_audit(c, 'VERIFICATION', 'Postcondition verified' if verified else 'Postcondition not yet established', **c['verification'])
        if verified:
            outcome(c, 'RESOLVED', 'Required postcondition verified from payment evidence.')
            w.update(stage='done', status='SUCCEEDED', ended_at=iso())
        elif stage == 'verify2' or t['canonical_state'] in ('UNKNOWN', 'CONFLICT', 'DISPUTED'):
            escalate(db, c, 'Required postcondition was not observed after bounded verification.')
        else:
            outcome(c, 'MONITORING', 'Postcondition not yet established; another evidence check is scheduled.')
            w['stage'] = 'verify2'; w['next_at'] = (now()+timedelta(seconds=5)).isoformat()
    else: raise HTTPException(422, 'Unsupported stage')
    w['receipts'][stage] = iso()
    if stage != 'action' and w['stage'] not in ('verify1', 'verify2'): w['next_at'] = iso()
    c['response'] = explain(c.get('twin', t), c['status'], c['intent'], c['language'])
    c['updated_at'] = iso(); save(db, 'case', c)
    return c

class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str
    execution_id: str = Field(min_length=1, max_length=100)

@router.post('/workflow/{case_id}/{stage}', dependencies=[Depends(workflow_auth)])
def step(case_id: str, stage: Literal['evidence', 'truth', 'policy', 'action', 'verify1', 'verify2', 'escalate'], body: WorkflowStep):
    with lock, transaction() as db:
        c = get(db, case_id, 'case')
        if c['workflow']['executor'] != 'n8n' or c['workflow']['id'] != body.run_id: raise HTTPException(409, 'Workflow run mismatch')
        c = advance(db, c, stage, body.execution_id); db.commit()
        return c

@router.post('/cases/{case_id}/replay')
def replay(case_id: str, p=Depends(operations)):
    with lock, transaction() as db:
        c = scoped(db, case_id, 'case', p)
        if not c['actions']: raise HTTPException(409, 'No action exists to replay')
        a = c['actions'][0]
        stored = get(db, a['id'], 'action')
        append_audit(c, 'IDEMPOTENCY', 'Safe retry: duplicate side effect prevented.', action_id=stored['id'], idempotency_key=stored['idempotency_key'], first_call_ref=stored['id'], result='EXISTING_ACTION_RETURNED')
        save(db, 'case', c); db.commit(); return c

def tick():
    """Persisted local runner plus independent synthetic notification delivery and watchdog."""
    with lock, transaction() as db:
        for d in all_rows(db, 'delivery'):
            if d['delivered'] or d['drop'] or now() < datetime.fromisoformat(d['due_at']): continue
            p = get(db, d['payment_id'], 'payment')
            e = event(p['id'], 'MERCHANT_ACK', amount=p['events'][0]['amount_paise'], index=100, actor='merchant-simulator', tool_call_id=d['action_id'], idempotency_key=d['key'])
            e['event_id'] = d['id']; e['timestamp'] = iso()
            p['events'].append(e); d['delivered'] = True
            save(db, 'payment', p); save(db, 'delivery', d)
        for original in all_rows(db, 'case'):
            c = json.loads(json.dumps(original)); w = c.get('workflow')
            if not w or c['status'] in ('RESOLVED', 'ESCALATED'): continue
            if now() >= datetime.fromisoformat(w['deadline']):
                escalate(db, c, 'Workflow deadline exceeded; execution may be unavailable.'); continue
            if w['executor'] == 'local' and now() >= datetime.fromisoformat(w['next_at']): advance(db, c, w['stage'])
        db.commit()

def enrich_outcomes():
    if not ai_configured(): return
    with Session() as db:
        pending = next((c for c in all_rows(db, 'case') if c['status'] in ('RESOLVED','ESCALATED') and c.get('twin') and not c.get('explanation_attempted')), None)
    if not pending: return
    explanation = grounded_explanation(pending)
    with lock, transaction() as db:
        c = get(db, pending['id'], 'case')
        c['explanation_attempted'] = True
        if explanation:
            c['explanation'] = explanation
            append_audit(c, 'GROUNDED_EXPLANATION', 'Gemini selected verified facts; all output facts validated.', model=explanation['model'], evidence_refs=explanation['evidence_refs'])
        else: append_audit(c, 'AI_FALLBACK', 'Gemini explanation unavailable; verified deterministic explanation retained.')
        save(db, 'case', c); db.commit()

class Simulation(BaseModel):
    scenario: Literal['confirmation', 'pending', 'settlement', 'conflict', 'notification_failure', 'settled']

@router.post('/simulator/payments')
def simulate_payment(body: Simulation, p=Depends(operations)):
    """Fresh evidence per rehearsal; historical cases and audit are never deleted."""
    name = body.scenario; label, kinds = SCENARIOS[name]; payment_id = uid('pay_'+name)
    with lock, transaction() as db:
        value = {'id': payment_id, 'merchant_id': p.get('merchant_id', 'm_001'), 'customer_id': 'customer_001', 'label': label,
            'scenario': name, 'events': [event(payment_id, kind, index=i) for i, kind in enumerate(kinds)]}
        save(db, 'payment', value); db.commit()
        return {'id': payment_id, 'label': label, 'state': twin(payment_id, value['events'])['canonical_state'], 'amount_paise': value['events'][0]['amount_paise']}

class EvidenceInput(BaseModel):
    type: Literal['CAPTURED', 'MERCHANT_ACK', 'SETTLEMENT_PENDING', 'SETTLED', 'FAILED', 'REFUNDED', 'REVERSED', 'DISPUTED']
    request_id: str = Field(min_length=8, max_length=100)

@router.post('/simulator/payments/{payment_id}/events')
def add_synthetic_event(payment_id: str, body: EvidenceInput, p=Depends(operations)):
    with lock, transaction() as db:
        value = scoped(db, payment_id, 'payment', p)
        event_id = 'sim_'+digest({'payment': payment_id, 'request': body.request_id})[:24]
        prior = next((e for e in value['events'] if e['event_id'] == event_id), None)
        if prior:
            if prior['type'] != body.type: raise HTTPException(409, 'Event request ID already used')
        else:
            e = event(payment_id, body.type, amount=value['events'][0]['amount_paise'], index=len(value['events']), actor='operations-simulator', idempotency_key=body.request_id)
            e.update(event_id=event_id, timestamp=iso()); value['events'].append(e)
            save(db, 'payment', value); db.commit()
        return twin(payment_id, value['events'])
