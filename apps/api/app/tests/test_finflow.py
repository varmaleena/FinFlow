import os
import tempfile
os.environ['DATABASE_URL'] = 'sqlite:///' + tempfile.mktemp(suffix='.db')
os.environ['MONITOR_INTERVAL'] = '3600'
os.environ['WORKFLOW_SECRET'] = 'test-workflow-secret'
os.environ.pop('N8N_WEBHOOK_URL', None)
from fastapi.testclient import TestClient
from apps.api.app.main import app
from apps.api.app.domain.money_twin import reconstruct
from apps.api.app.domain.policy_engine import evaluate
from apps.api.app.domain.financial import simulate, reconcile
import pytest

HEADERS = {'Authorization': 'Bearer finflow-local-demo', 'X-Merchant-Id': 'm_001'}

@pytest.fixture
def client():
    with TestClient(app, headers=HEADERS) as c:
        c.post('/v1/demo/reset')
        yield c

def case(client, message='Check this payment', tx='tx_1001'):
    r = client.post('/v1/conversations', json={'message': message, 'transaction_id': tx})
    assert r.status_code == 200, r.text
    return r.json()

def execute(client, c):
    return client.post('/v1/actions/execute', json={'policy_decision_id': c['policy']['id'], 'idempotency_key': c['id'] + ':action'})

def test_notification_verified_and_idempotent(client):
    c = case(client)
    assert c['twin']['derived_state'] == 'PAID_CONFIRMATION_MISSING'
    result = execute(client, c)
    assert result.status_code == 200
    assert result.json()['verified']
    assert execute(client, c).json()['id'] == result.json()['id']
    resolved = client.get('/v1/cases/' + c['id']).json()
    assert resolved['status'] == 'RESOLVED'
    audit = client.get('/v1/audit/' + c['id']).json()
    assert len([e for e in audit if e['event_type'] == 'VERIFIED']) == 1
    assert audit[-1]['policy_decision_id'] == c['policy']['id']

def test_pending_monitor_escalates(client):
    c = case(client, tx='tx_1002')
    assert 'Do not collect again' in c['response']
    action = execute(client, c).json()
    assert not action['verified']
    url = '/internal/actions/' + action['id'] + '/step'
    assert client.post(url).status_code == 403
    for _ in range(2): response = client.post(url, headers={'X-Finflow-Secret': 'test-workflow-secret'})
    assert response.json()['status'] == 'FAILED'
    assert client.get('/v1/cases/' + c['id']).json()['status'] == 'ESCALATED'
    assert client.post(url, headers={'X-Finflow-Secret': 'test-workflow-secret'}).json()['checks'] == 2

def test_conflict_and_merchant_isolation(client):
    assert client.post('/v1/tools/payment-twin', json={'transaction_id':'tx_1005'}).status_code == 404
    c = client.post('/v1/conversations', headers={'X-Merchant-Id':'m_002'}, json={'message':'Check payment tx_1005'}).json()
    assert c['status'] == 'ESCALATED'
    assert not c['policy']['allowed']
    assert client.get('/v1/cases/'+c['id']).status_code == 404

def test_auth(client):
    assert client.get('/v1/dashboard', headers={'Authorization':''}).status_code == 401
    assert client.get('/v1/dashboard', headers={'X-Merchant-Id':'unknown'}).status_code == 403

def test_high_risk_denied(client):
    c = case(client)
    for action in ('REFUND','REVERSAL','CHANGE_BANK','DISBURSE_LOAN'):
        p = client.post('/v1/actions/evaluate', json={'case_id':c['id'],'action_type':action}).json()
        assert not p['allowed'] and p['risk_tier'] == 'R3'
        assert client.post('/v1/actions/execute',json={'policy_decision_id':p['id'],'idempotency_key':'denied:'+action}).status_code == 403

def test_unknown_and_ambiguous_fail_closed(client):
    c = case(client, message='Check a payment', tx=None)
    assert c['status'] == 'OPEN' and 'policy' not in c
    assert client.post('/v1/conversations',json={'message':'payment tx_9999'}).status_code == 404
    assert client.post('/v1/conversations',json={'message':''}).status_code == 422

def test_settlement_arithmetic(client):
    c = case(client, message='Explain my settlement', tx=None)
    assert c['result']['expected_net'] == 26607.75
    assert c['result']['difference'] == 0
    assert c['status'] == 'RESOLVED'
    c = client.post('/v1/conversations',headers={'X-Merchant-Id':'m_002'},json={'message':'Explain settlement'}).json()
    assert c['result']['difference'] == 1290
    assert c['status'] == 'ESCALATED'

def test_credit_and_validation(client):
    value = client.post('/v1/tools/credit-simulate',json={'principal':50000,'months':12,'annual_rate':18}).json()['result']
    assert value['monthly_payment'] == 4584
    assert simulate(12000,12,0)['monthly_payment'] == 1000
    for payload in ({'principal':-1},{'months':0},{'months':100},{'annual_rate':-1}):
        assert client.post('/v1/tools/credit-simulate',json=payload).status_code == 422

def test_cashflow_and_reset(client):
    snap = client.get('/v1/tools/cashflow/m_001').json()['result']
    assert snap['net'] == snap['inflow'] - snap['outflow']
    assert client.get('/v1/tools/cashflow/m_002').status_code == 403
    execute(client, case(client))
    client.post('/v1/demo/reset')
    assert client.get('/v1/dashboard').json()['cases'] == []
    assert case(client)['twin']['derived_state'] == 'PAID_CONFIRMATION_MISSING'

@pytest.mark.parametrize('state,action,attempts,allowed',[
    ('PAID_CONFIRMATION_MISSING','RETRY_MERCHANT_NOTIFICATION',2,True),
    ('PAID_CONFIRMATION_MISSING','RETRY_MERCHANT_NOTIFICATION',3,False),
    ('EVIDENCE_CONFLICT','RETRY_MERCHANT_NOTIFICATION',0,False),
    ('MISSING_EVIDENCE','MONITOR_PAYMENT',0,False),
    ('PENDING_FINALITY','MONITOR_PAYMENT',0,True),
    ('PAID','MONITOR_PAYMENT',0,False)])
def test_policy_matrix(state,action,attempts,allowed):
    assert evaluate(state,action,attempts)['allowed'] == allowed

def test_missing_and_conflicting_evidence():
    base = {'id':'test','amount':2000,'customer_debit':'DEBITED','rail_status':'SUCCESS','paytm_status':'SUCCESS','confirmation':'FAILED'}
    assert reconstruct({**base,'paytm_status':None})['derived_state'] == 'MISSING_EVIDENCE'
    assert reconstruct({**base,'customer_debit':'REVERSED'})['derived_state'] == 'EVIDENCE_CONFLICT'

def test_stale_policy_denied(client):
    first, second = case(client), case(client)
    execute(client, first)
    assert execute(client, second).status_code == 403

def test_voice_missing_configuration(client):
    if not os.getenv('SARVAM_API_KEY'):
        assert client.post('/v1/voice/transcribe',files={'file':('audio.wav',b'demo','audio/wav')}).status_code == 503

def test_numbered_monitor_steps_idempotent(client):
    action = execute(client, case(client,tx='tx_1002')).json()
    url = '/internal/actions/'+action['id']+'/step'
    headers = {'X-Finflow-Secret':'test-workflow-secret'}
    assert client.post(url+'?step=2',headers=headers).status_code == 409
    assert client.post(url+'?step=1',headers=headers).json()['checks'] == 1
    assert client.post(url+'?step=1',headers=headers).json()['checks'] == 1
    assert client.post(url+'?step=2',headers=headers).json()['checks'] == 2

def test_golden_dataset():
    import json
    from pathlib import Path
    for line in Path('data/evaluation/cases.jsonl').read_text().splitlines():
        c = json.loads(line)
        result = reconstruct({'id':c['id'],'amount':100,'rail_status':c['rail'],'paytm_status':c['provider'],'customer_debit':c['debit'],'confirmation':c['confirmation']})
        assert result['derived_state'] == c['expected']

def test_local_memory(client):
    result = client.get('/v1/tools/memory?query=pending payment').json()['result']
    assert result['authoritative'] is False
    assert result['provider'] == 'local_playbooks'
