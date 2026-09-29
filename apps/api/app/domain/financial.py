from decimal import Decimal, ROUND_HALF_UP

def money(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def reconcile(batch):
    expected = Decimal(str(batch['gross'])) - sum((Decimal(str(batch[k])) for k in ('refunds', 'fees', 'adjustments')), Decimal(0))
    difference = money(expected - Decimal(str(batch['credited'])))
    return {**batch, 'expected_net': money(expected), 'difference': difference, 'derived_state': 'RECONCILED' if abs(difference) <= .01 else 'SETTLEMENT_MISMATCH', 'evidence_ids': [f"{batch['id']}:{x}" for x in ('gross', 'refunds', 'fees', 'adjustments', 'credited')]}

def simulate(principal, months, annual_rate):
    p, r = Decimal(str(principal)), Decimal(str(annual_rate)) / 1200
    emi = p / months if r == 0 else p * r * (1+r)**months / ((1+r)**months-1)
    return {'principal': money(p), 'months': months, 'annual_rate': annual_rate, 'monthly_payment': money(emi), 'total_repayment': money(emi * months), 'total_interest': money(emi * months-p), 'assumptions': 'Illustrative reducing-balance rate; excludes fees and taxes. Not an offer or approval.', 'evidence_ids': ['credit_assumptions_v1']}
