def reconstruct(tx):
    required = ('customer_debit', 'rail_status', 'paytm_status', 'confirmation')
    conflicts = []
    if any(not tx.get(k) for k in required):
        state = 'MISSING_EVIDENCE'
    elif {tx['rail_status'], tx['paytm_status']} == {'SUCCESS', 'FAILED'} or (tx['rail_status'] == 'SUCCESS' and tx['customer_debit'] in ('NOT_DEBITED', 'REVERSED')):
        state = 'EVIDENCE_CONFLICT'
        conflicts = ['Payment rail, provider or debit evidence disagree.']
    elif 'PENDING' in (tx['rail_status'], tx['paytm_status']):
        state = 'PENDING_FINALITY'
    elif tx['rail_status'] == tx['paytm_status'] == 'SUCCESS' and tx['customer_debit'] == 'DEBITED':
        state = 'PAID' if tx['confirmation'] == 'ACKNOWLEDGED' else 'PAID_CONFIRMATION_MISSING'
    elif tx['rail_status'] == tx['paytm_status'] == 'FAILED':
        state = 'FAILED'
    else:
        state = 'MISSING_EVIDENCE'
    return {'derived_state': state, 'canonical_amount': tx['amount'], 'currency': 'INR', 'conflicts': conflicts,
            'evidence_ids': [f"{tx['id']}:debit", f"{tx['id']}:rail", f"{tx['id']}:provider", f"{tx['id']}:confirmation"],
            'customer_debit': tx.get('customer_debit'), 'rail': tx.get('rail_status'), 'provider': tx.get('paytm_status'), 'confirmation': tx.get('confirmation')}
