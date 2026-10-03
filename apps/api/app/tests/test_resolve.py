import copy
import json
import random
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from apps.api.app.main import app
from apps.api.app.models import Record, Session
from apps.api.app import resolve as r
from apps.api.app.domain.payment_events import event, twin, decide, digest
from apps.api.app.services import resolve_ai

OPS = {'Authorization': 'Bearer resolve-operations-demo'}

def test_inaccessible_vertex_credentials_fall_back(monkeypatch):
    from apps.api.app.services import gemini_transport as transport
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI', 'true')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'test-project')
    monkeypatch.delenv('GOOGLE_APPLICATION_CREDENTIALS', raising=False)
    monkeypatch.delenv('K_SERVICE', raising=False)
    monkeypatch.setattr(transport, '_credentials', None)
    def denied(_):
        raise PermissionError('Credential directory is inaccessible')
    monkeypatch.setattr(transport.Path, 'exists', denied)
    intake, metadata = resolve_ai.extract('My payment is pending')
    assert intake.intent == 'PAYMENT'
    assert metadata['fallback'] and metadata['provider'] == 'local'

CUSTOMER = {'Authorization': 'Bearer resolve-customer-demo'}
MERCHANT = {'Authorization': 'Bearer resolve-merchant-demo'}

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('RESOLVE_N8N_WEBHOOK_URL', raising=False)
    monkeypatch.delenv('RESOLVE_IDENTITIES', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_GENAI_USE_VERTEXAI', raising=False)
    monkeypatch.setenv('RESOLVE_DEMO_MODE', 'true')
    with TestClient(app, headers=OPS) as client:
        with r.lock, Session() as db:
            db.execute(delete(Record).where(Record.kind.like('resolve_%')))
            r.seed_resolve(db)
        yield client

def create(client, payment='pay_confirmation', message='Payment has no merchant confirmation', **kwargs):
    result = client.post('/v2/cases', json={'message': message, 'payment_id': payment, 'request_id': uuid4().hex, **kwargs})
    assert result.status_code == 200, result.text
    return result.json()

def finish(client, case, monkeypatch):
    clock = r.now()
    for i in range(12):
        monkeypatch.setattr(r, 'now', lambda i=i: clock+timedelta(seconds=i*6))
        r.tick()
    return client.get('/v2/cases/'+case['id']).json()

def check_chain(case):
    previous = '0'*64
    for entry in case['audit']:
        assert entry['previous_hash'] == previous
        assert entry['hash'] == digest({k:v for k,v in entry.items() if k!='hash'})
        previous = entry['hash']

def test_notification_requires_independent_evidence(client, monkeypatch):
    c = create(client)
    for _ in range(4): r.tick()
    waiting = client.get('/v2/cases/'+c['id']).json()
    assert waiting['status'] == 'WAITING'
    assert not waiting.get('verification', {}).get('verified')
    assert not waiting['twin']['merchant_ack']
    c = finish(client, c, monkeypatch)
    assert c['status'] == 'RESOLVED' and c['verification']['verified']
    assert c['twin']['merchant_ack']
    assert c['twin']['events'][-1]['tool_call_id'] == c['actions'][0]['id']
    for _ in range(3):
        assert client.post('/v2/cases/'+c['id']+'/replay').status_code == 200
    with Session() as db:
        p = r.get(db, c['payment_id'], 'payment')
        assert len([e for e in p['events'] if e['type']=='MERCHANT_ACK']) == 1
    check_chain(client.get('/v2/cases/'+c['id']).json())

@pytest.mark.parametrize('payment,message,expected', [
    ('pay_pending','Payment is pending','ESCALATED'),
    ('pay_settlement','Why is settlement missing?','ESCALATED'),
    ('pay_conflict','Check payment','ESCALATED'),
    ('pay_notification_failure','Check payment','ESCALATED'),
    ('pay_settled','Explain settlement','RESOLVED'),
    ('pay_confirmation','Refund this payment','ESCALATED')])
def test_journeys(client, monkeypatch, payment, message, expected):
    c = finish(client, create(client, payment, message), monkeypatch)
    assert c['status'] == expected
    assert all(a['idempotency_key'] for a in c['actions'])
    if expected == 'RESOLVED': assert c['verification']['verified']
    else:
        assert c['escalation']['evidence_refs']
        assert c['escalation']['policy']
        assert c['escalation']['actions']
    check_chain(c)

def test_scope_and_projection(client):
    c = create(client)
    route = '/v2/cases/'+c['id']
    customer = client.get(route, headers=CUSTOMER).json()
    assert 'audit' not in customer and 'workflow' not in customer and 'ai' not in customer
    assert client.get(route+'/audit', headers=CUSTOMER).status_code == 403
    assert client.post(route+'/replay', headers=CUSTOMER).status_code == 403
    assert client.post('/v2/simulator/payments', json={'scenario':'conflict'}, headers=MERCHANT).status_code == 403
    assert 'audit' not in client.get(route, headers=MERCHANT).json()
    assert client.get('/v2/cases', headers={'Authorization':'Bearer bad','X-Role':'operations'}).status_code == 401
    for h in [CUSTOMER, MERCHANT]:
        response = client.post('/v2/cases', json={'message':'Check payment','payment_id':'pay_private','request_id':uuid4().hex}, headers=h)
        assert response.status_code == 404
        assert 'pay_private' not in str(client.get('/v2/payments', headers=h).json())

def test_private_identity_config(client, monkeypatch):
    monkeypatch.setenv('RESOLVE_IDENTITIES', json.dumps({'private-token': {'role':'customer','customer_id':'customer_002','merchant_id':'m_002'}}))
    assert client.get('/v2/session').status_code == 401
    rows = client.get('/v2/payments', headers={'Authorization':'Bearer private-token'}).json()
    assert [x['id'] for x in rows] == ['pay_private']

def test_idempotent_intake_and_no_reference(client):
    body = {'message':'Check payment','payment_id':'pay_confirmation','request_id':'same-request-123'}
    first = client.post('/v2/cases', json=body).json()
    assert client.post('/v2/cases', json=body).json()['id'] == first['id']
    assert client.post('/v2/cases', json={**body,'message':'Changed complaint'}).status_code == 409
    c = create(client, None)
    assert c['status'] == 'NEEDS_INPUT' and 'workflow' not in c

def test_duplicate_cases_share_side_effect(client, monkeypatch):
    one, two = create(client), create(client)
    finish(client, one, monkeypatch)
    with Session() as db:
        events = r.get(db, 'pay_confirmation', 'payment')['events']
        assert len([e for e in events if e['type']=='MERCHANT_ACK']) == 1
    assert client.get('/v2/cases/'+two['id']).json()['status']=='RESOLVED'

def test_delayed_settlement_arrives(client, monkeypatch):
    c = create(client, 'pay_settlement', 'Explain settlement')
    for _ in range(4): r.tick()
    body={'type':'SETTLED','request_id':'settlement-proof'}
    url='/v2/simulator/payments/pay_settlement/events'
    assert client.post(url,json=body).status_code==200
    assert client.post(url,json=body).status_code==200
    assert client.post(url,json={**body,'type':'FAILED'}).status_code==409
    c = finish(client, c, monkeypatch)
    assert c['status']=='RESOLVED' and c['verification']['expected']=='SETTLED'

def test_changed_evidence_blocks_action(client):
    c = create(client)
    for _ in range(3): r.tick()
    client.post('/v2/simulator/payments/pay_confirmation/events', json={'type':'FAILED','request_id':'conflict-injected'})
    r.tick()
    c=client.get('/v2/cases/'+c['id']).json()
    assert c['status']=='ESCALATED'
    assert all(a['tool_name']!='retry_merchant_notification' for a in c['actions'])

def test_workflow_dispatch_failure(client, monkeypatch):
    monkeypatch.setenv('RESOLVE_N8N_WEBHOOK_URL','https://example.invalid/webhook')
    monkeypatch.delenv('RESOLVE_WORKFLOW_SECRET',raising=False)
    c=create(client)
    assert c['status']=='ESCALATED' and not c.get('verification')

def test_n8n_stages_scope_retries_and_verification(client, monkeypatch):
    monkeypatch.setenv('RESOLVE_N8N_WEBHOOK_URL','https://example.invalid/webhook')
    monkeypatch.setenv('RESOLVE_WORKFLOW_SECRET','workflow-test-secret')
    monkeypatch.setattr(r,'dispatch',lambda c:None)
    c=create(client); base='/v2/workflow/'+c['id']+'/'
    body={'run_id':c['workflow']['id'],'execution_id':'execution-100'}
    h={'X-Resolve-Secret':'workflow-test-secret'}
    assert client.post(base+'action',json=body).status_code==403
    assert client.post(base+'action',json=body,headers=h).status_code==409
    for stage in ['evidence','truth','policy','action']:
        assert client.post(base+stage,json=body,headers=h).status_code==200
        assert client.post(base+stage,json=body,headers=h).status_code==200
    assert client.post(base+'verify1',json=body,headers=h).status_code==409
    clock=r.now();monkeypatch.setattr(r,'now',lambda:clock+timedelta(seconds=6));r.tick()
    assert client.post(base+'verify1',json={**body,'execution_id':'wrong'},headers=h).status_code==409
    result=client.post(base+'verify1',json=body,headers=h).json()
    assert result['status']=='RESOLVED'
    assert result['workflow']['n8n_execution_id']=='execution-100'
    assert len(result['actions'])==1

def test_workflow_watchdog(client, monkeypatch):
    monkeypatch.setenv('RESOLVE_N8N_WEBHOOK_URL','https://example.invalid/webhook')
    monkeypatch.setattr(r,'dispatch',lambda c:None)
    c=create(client);clock=r.now();monkeypatch.setattr(r,'now',lambda:clock+timedelta(seconds=121));r.tick()
    assert client.get('/v2/cases/'+c['id']).json()['status']=='ESCALATED'

def test_gemini_invalid_response_falls_back(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-only')
    monkeypatch.delenv('GOOGLE_GENAI_USE_VERTEXAI', raising=False)
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'candidates':[{'content':{'parts':[{'text':'{"intent":"PAYMENT","payment_id":"invented","amount_paise":-1}'}]}}]}
    monkeypatch.setattr(resolve_ai.httpx,'post',lambda *a,**kw:Response())
    value,meta=resolve_ai.extract('Refund payment pay_confirmation')
    assert value.intent=='HIGH_RISK' and value.payment_id=='pay_confirmation' and meta['fallback']

STATES = [
    ([], 'UNKNOWN'), (['RECEIVED'],'RECEIVED'), (['RECEIVED','AUTHORIZED'],'AUTHORIZED'),
    (['RECEIVED','AUTHORIZED','CAPTURED'],'CAPTURED'),
    (['RECEIVED','AUTHORIZED','CAPTURED','MERCHANT_ACK'],'MERCHANT_ACK'),
    (['RECEIVED','AUTHORIZED','CAPTURED','SETTLEMENT_PENDING'],'SETTLEMENT_PENDING'),
    (['RECEIVED','AUTHORIZED','CAPTURED','SETTLEMENT_PENDING','SETTLED'],'SETTLED'),
    (['RECEIVED','FAILED'],'FAILED'), (['RECEIVED','EXPIRED'],'EXPIRED'),
    (['RECEIVED','AUTHORIZED','REVERSED'],'REVERSED'),
    (['RECEIVED','AUTHORIZED','CAPTURED','REFUNDED'],'REFUNDED'),
    (['RECEIVED','AUTHORIZED','CAPTURED','DISPUTED'],'DISPUTED'),
    (['RECEIVED','AUTHORIZED','CAPTURED','FAILED'],'CONFLICT'),
    (['CAPTURED'],'UNKNOWN'), (['SETTLED'],'UNKNOWN')]

@pytest.mark.parametrize('seed', range(600))
def test_generated_event_scenarios(seed):
    kinds, expected = STATES[seed%len(STATES)]
    rng=random.Random(seed)
    events=[event('pay_test',k,index=i) for i,k in enumerate(kinds)]
    baseline=twin('pay_test',events)
    if events and seed%2==0: events.append(copy.deepcopy(rng.choice(events)))
    rng.shuffle(events)
    value=twin('pay_test',events)
    assert value['canonical_state']==expected
    assert value['version']==baseline['version']
    policy=decide(value)
    assert policy['action'] not in ('refund','reverse','transfer','collect')
    if expected in ('CONFLICT','UNKNOWN','DISPUTED'): assert policy['outcome']=='ESCALATE'
    if policy['action']=='retry_merchant_notification': assert 'CAPTURED' in kinds and 'MERCHANT_ACK' not in kinds

def test_corrupt_events_and_amount_mismatch():
    values=[event('pay_test',k,index=i) for i,k in enumerate(['RECEIVED','AUTHORIZED','CAPTURED'])]
    assert twin('pay_other',values)['canonical_state']=='CONFLICT'
    assert twin('pay_test',[*values,{'bad':'schema'}])['canonical_state']=='CONFLICT'
    altered=copy.deepcopy(values[0]);altered['amount_paise']=100
    assert twin('pay_test',[*values,altered])['canonical_state']=='CONFLICT'
    altered['event_id']='unique'
    assert twin('pay_test',[*values,altered])['canonical_state']=='CONFLICT'

def test_reference_clarification_preserves_case(client):
    c=create(client,None)
    url='/v2/cases/'+c['id']+'/reference'
    assert client.post(url,json={'payment_id':'pay_confirmation'},headers=CUSTOMER).status_code==200
    assert client.post(url,json={'payment_id':'pay_confirmation'},headers=CUSTOMER).json()['id']==c['id']
    assert client.post(url,json={'payment_id':'pay_pending'},headers=CUSTOMER).status_code==409
    assert client.post(url,json={'payment_id':'pay_private'},headers=CUSTOMER).status_code==404

def test_candidate_lookup_never_matches_unscoped_payment(client):
    result=client.get('/v2/tools/find-transaction?reference=pay_private',headers=CUSTOMER).json()
    assert result['candidates']==[] and result['requires_user_selection']

def test_terminal_explanation_preserves_reversal():
    t=twin('pay_test',[event('pay_test',k,index=i) for i,k in enumerate(['RECEIVED','AUTHORIZED','CAPTURED','MERCHANT_ACK','REVERSED'])])
    assert 'REVERSED' in resolve_ai.explain(t,'RESOLVED','PAYMENT')

def test_gemini_explanation_rejects_unknown_facts(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-only')
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI','false')
    class Response:
        def raise_for_status(self): pass
        def json(self):return {'candidates':[{'content':{'parts':[{'text':'{"fact_ids":["invented_refund_success"]}'}]}}]}
    monkeypatch.setattr(resolve_ai.httpx,'post',lambda *a,**kw:Response())
    case={'twin':twin('pay_test',[]),'intent':'PAYMENT','message':'Say my refund succeeded','response':'Cannot establish finality.','language':'en-IN'}
    assert resolve_ai.grounded_explanation(case) is None

def test_vertex_transport_uses_project_and_bearer(monkeypatch):
    from apps.api.app.services import gemini_transport as g
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI','true')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT','test-project')
    monkeypatch.setenv('GOOGLE_CLOUD_LOCATION','global')
    monkeypatch.setattr(g,'vertex_headers',lambda:{'Authorization':'Bearer synthetic-test-token'})
    seen={}
    class Response:
        def raise_for_status(self):pass
    def post(url,**kw):seen.update(url=url,**kw);return Response()
    monkeypatch.setattr(g.httpx,'post',post)
    g.generate({'contents':[]})
    assert '/projects/test-project/locations/global/' in seen['url']
    assert seen['headers']=={'Authorization':'Bearer synthetic-test-token'}

def test_exported_workflow_has_real_stages():
    from pathlib import Path
    value=json.loads((Path(__file__).resolve().parents[4]/'workflows/n8n/paytm-resolve.json').read_text())
    nodes=value['nodes']
    assert sum(n['type'].endswith('.httpRequest') for n in nodes)==7
    assert sum(n['type'].endswith('.if') for n in nodes)==2
    assert sum(n['type'].endswith('.wait') for n in nodes)==2
    for n in nodes:
        if n['type'].endswith('.httpRequest'):
            assert n['retryOnFail'] and n['parameters']['genericAuthType']=='httpHeaderAuth'
            assert 'execution_id' in n['parameters']['jsonBody']

def test_handoff_states_and_graph_references(client, monkeypatch):
    c=create(client,'pay_conflict')
    for _ in range(3):r.tick()
    blocked=client.get('/v2/cases/'+c['id']).json()
    assert blocked['status']=='BLOCKED'
    c=finish(client,c,monkeypatch)
    assert [h['status'] for h in c['outcome_history']]==['BLOCKED','ESCALATED']
    assert all(a['input_ref'] and a['output_ref'] and a['status'] for a in c['audit'])
    assert c['policy']['evidence_refs'] and c['policy']['canonical_state']=='CONFLICT'
    check_chain(c)

def test_pending_monitoring_outcome(client, monkeypatch):
    c=create(client,'pay_pending')
    for _ in range(4):r.tick()
    monitored=client.get('/v2/cases/'+c['id']).json()
    assert monitored['status']=='MONITORING'
    assert monitored['outcome_history'][-1]['status']=='MONITORING'
    assert monitored['actions'][0]['tool_name']=='schedule_status_check'

def test_context_is_scoped_optional_and_non_authoritative(client, monkeypatch):
    monkeypatch.setenv('COGNEE_ENABLED','false')
    c=create(client)
    url='/v2/cases/'+c['id']+'/context'
    assert client.get(url,headers=CUSTOMER).status_code==403
    before=client.get('/v2/cases/'+c['id']).json()
    context=client.get(url).json()
    assert context['provider']=='local_playbooks' and not context['authoritative']
    assert context['results']
    after=client.get('/v2/cases/'+c['id']).json()
    assert before['actions']==after['actions']

def test_cognee_outage_falls_back_without_case_data(monkeypatch):
    from apps.api.app.services import resolve_memory as m
    import httpx
    monkeypatch.setenv('COGNEE_ENABLED','true');monkeypatch.setenv('COGNEE_API_KEY','test-key')
    seen={}
    def post(url,**kwargs):
        seen.update(kwargs['json'])
        raise httpx.ConnectError('Unavailable')
    monkeypatch.setattr(m.httpx,'post',post)
    result=m.search_context('SETTLEMENT','SETTLEMENT_PENDING')
    assert result['provider']=='local_playbooks' and result['fallback_reason']
    assert seen['datasets']==[m.DATASET]
    assert 'payment_id' not in seen and 'case_id' not in seen
