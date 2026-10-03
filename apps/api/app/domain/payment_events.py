"""Validated, order-independent synthetic payment ledger. No language model writes here."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

EventType = Literal['RECEIVED', 'AUTHORIZED', 'CAPTURED', 'MERCHANT_ACK', 'SETTLEMENT_PENDING', 'SETTLED', 'FAILED', 'REVERSED', 'EXPIRED', 'REFUNDED', 'DISPUTED']

class PaymentEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str
    payment_id: str
    type: EventType
    timestamp: datetime
    source: Literal['paytm-simulator', 'merchant-simulator', 'settlement-simulator']
    amount_paise: int = Field(gt=0, strict=True)
    actor: str = 'simulator'
    tool_call_id: str | None = None
    idempotency_key: str | None = None

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

def event(payment_id, kind, amount=200000, index=0, **extra):
    return PaymentEvent(event_id=f'{payment_id}:{kind}:{index}', payment_id=payment_id, type=kind,
        timestamp=datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)+timedelta(seconds=index),
        source='merchant-simulator' if kind == 'MERCHANT_ACK' else 'settlement-simulator' if kind in ('SETTLED', 'SETTLEMENT_PENDING') else 'paytm-simulator',
        amount_paise=amount, **extra).model_dump(mode='json')

def twin(payment_id, raw_events):
    unique, errors = {}, []
    for raw in raw_events:
        try:
            e = PaymentEvent.model_validate(raw).model_dump(mode='json')
            if e['payment_id'] != payment_id or datetime.fromisoformat(e['timestamp']).tzinfo is None:
                errors.append('Invalid payment scope or timestamp'); continue
            if e['event_id'] in unique and unique[e['event_id']] != e:
                errors.append('An event identifier has conflicting payloads')
                e = min((unique[e['event_id']], e), key=digest)
            unique[e['event_id']] = e
        except (ValueError, TypeError): errors.append('Invalid event schema')
    events = sorted(unique.values(), key=lambda e: (datetime.fromisoformat(e['timestamp']), e['event_id']))
    kinds = {e['type'] for e in events}
    if len({e['amount_paise'] for e in events}) > 1: errors.append('Amounts disagree')
    if 'CAPTURED' in kinds and kinds & {'FAILED', 'EXPIRED'}: errors.append('Capture conflicts with failed/expired evidence')
    if len(kinds & {'FAILED', 'EXPIRED', 'REVERSED', 'REFUNDED'}) > 1: errors.append('Terminal evidence conflicts')
    prerequisites = {'AUTHORIZED': {'RECEIVED'}, 'CAPTURED': {'RECEIVED', 'AUTHORIZED'},
        'MERCHANT_ACK': {'CAPTURED'}, 'SETTLEMENT_PENDING': {'CAPTURED'}, 'SETTLED': {'CAPTURED', 'SETTLEMENT_PENDING'},
        'REFUNDED': {'CAPTURED'}, 'REVERSED': {'AUTHORIZED'}, 'DISPUTED': {'CAPTURED'}}
    missing = sorted({p for k in kinds for p in prerequisites.get(k, set()) if p not in kinds})
    state = 'UNKNOWN'
    if errors: state = 'CONFLICT'
    elif not missing:
        for k in ['RECEIVED', 'AUTHORIZED', 'CAPTURED', 'MERCHANT_ACK', 'SETTLEMENT_PENDING', 'SETTLED', 'FAILED', 'EXPIRED', 'REVERSED', 'REFUNDED', 'DISPUTED']:
            if k in kinds: state = k
    transitions, previous = [], 'UNKNOWN'
    for e in events:
        transitions.append({**e, 'previous_state': previous, 'new_state': e['type'], 'payload_hash': digest(e), 'evidence_ref': e['event_id']})
        previous = e['type']
    return {'payment_id': payment_id, 'canonical_state': state, 'version': digest(events)[:16],
        'amount_paise': events[0]['amount_paise'] if events else None, 'currency': 'INR',
        'merchant_ack': 'MERCHANT_ACK' in kinds, 'settled': 'SETTLED' in kinds,
        'evidence_refs': [e['event_id'] for e in events], 'events': transitions, 'conflicts': errors,
        'missing_evidence': missing, 'final': state in ('SETTLED', 'MERCHANT_ACK', 'FAILED', 'EXPIRED', 'REFUNDED', 'REVERSED')}

POLICY_VERSION = 'resolve-2026-10-03.v1'
def decide(t, intent='PAYMENT'):
    state = t['canonical_state']
    action, outcome, reason = 'create_escalation', 'ESCALATE', 'Evidence is incomplete or conflicting; autonomous action is blocked.'
    if state in ('CONFLICT', 'UNKNOWN', 'DISPUTED') or intent == 'HIGH_RISK': pass
    elif intent == 'SETTLEMENT':
        if state == 'SETTLED': action, outcome, reason = 'explain_resolution', 'EXPLAIN', 'Capture and settlement are evidenced independently.'
        elif state in ('CAPTURED', 'MERCHANT_ACK', 'SETTLEMENT_PENDING'):
            action, outcome, reason = 'schedule_status_check', 'MONITOR', 'Payment capture is verified; settlement completion is not. Await settlement evidence.'
    elif state in ('CAPTURED', 'SETTLEMENT_PENDING', 'SETTLED') and not t['merchant_ack']:
        action, outcome, reason = 'retry_merchant_notification', 'ALLOW', 'Capture evidence is complete; only merchant acknowledgement is missing. No money movement.'
    elif state in ('RECEIVED', 'AUTHORIZED'):
        action, outcome, reason = 'schedule_status_check', 'MONITOR', 'Payment finality is not established. Do not collect again.'
    elif state in ('MERCHANT_ACK', 'SETTLEMENT_PENDING', 'SETTLED', 'FAILED', 'EXPIRED', 'REFUNDED', 'REVERSED'):
        action, outcome, reason = 'explain_resolution', 'EXPLAIN', 'Explain only the state established by ledger evidence.'
    return {'policy_version': POLICY_VERSION, 'outcome': outcome, 'action': action, 'allowed_actions': [action], 'reason': reason, 'twin_version': t['version']}

SCENARIOS = {
    'confirmation': ('Missing merchant confirmation', ['RECEIVED', 'AUTHORIZED', 'CAPTURED']),
    'pending': ('Payment pending after debit', ['RECEIVED', 'AUTHORIZED']),
    'settlement': ('Captured payment awaiting settlement', ['RECEIVED', 'AUTHORIZED', 'CAPTURED', 'MERCHANT_ACK', 'SETTLEMENT_PENDING']),
    'conflict': ('Conflicting provider evidence', ['RECEIVED', 'AUTHORIZED', 'CAPTURED', 'FAILED']),
    'notification_failure': ('Notification acknowledgement never arrives', ['RECEIVED', 'AUTHORIZED', 'CAPTURED']),
    'settled': ('Settlement completed', ['RECEIVED', 'AUTHORIZED', 'CAPTURED', 'MERCHANT_ACK', 'SETTLEMENT_PENDING', 'SETTLED']),
}
