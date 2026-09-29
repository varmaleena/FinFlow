HIGH_RISK = {'REFUND', 'REVERSAL', 'CHANGE_BANK', 'DISBURSE_LOAN'}

def evaluate(state, action, attempts=0):
    decision = {'action_type': action, 'allowed': False, 'risk_tier': 'R2', 'rule': 'default_deny_v1', 'reason': 'No matching policy.'}
    if action in HIGH_RISK:
        return {**decision, 'risk_tier': 'R3', 'reason': 'Human review required. Financial mutations are excluded from this demo.'}
    if state in ('EVIDENCE_CONFLICT', 'MISSING_EVIDENCE'):
        return {**decision, 'reason': 'Evidence is conflicting or incomplete; human review required.'}
    if action == 'RETRY_MERCHANT_NOTIFICATION' and state == 'PAID_CONFIRMATION_MISSING' and attempts < 3:
        return {**decision, 'allowed': True, 'risk_tier': 'R1', 'rule': 'payment_confirmation_retry_v1', 'reason': 'Payment confirmed by both sources; notification retry is within the three-attempt limit.'}
    if action == 'MONITOR_PAYMENT' and state == 'PENDING_FINALITY':
        return {**decision, 'allowed': True, 'rule': 'pending_monitor_v1', 'reason': 'Recheck twice, then escalate. Never collect a duplicate payment.'}
    return decision
