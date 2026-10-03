"""Real HTTP journey smoke test against a running local or hosted Resolve server."""
import argparse
import json
import os
import time
from uuid import uuid4
from pathlib import Path
import httpx

def run(url):
    result=[]
    with httpx.Client(base_url=url,headers={'Authorization':'Bearer '+os.getenv('RESOLVE_SMOKE_TOKEN','resolve-operations-demo')},timeout=45) as c:
        for scenario,message,expected in [
            ('confirmation','My payment has no merchant confirmation','RESOLVED'),
            ('pending','Is my pending payment complete?','ESCALATED'),
            ('settlement','Explain the missing settlement','ESCALATED'),
            ('conflict','Investigate this payment','ESCALATED'),
            ('notification_failure','Check payment acknowledgement','ESCALATED'),
            ('settled','Explain settlement','RESOLVED')]:
            response=c.post('/v2/simulator/payments',json={'scenario':scenario});response.raise_for_status();payment=response.json()
            started=time.monotonic()
            response=c.post('/v2/cases',json={'message':message,'payment_id':payment['id'],'request_id':uuid4().hex});response.raise_for_status();case=response.json()
            deadline=started+130
            while case['status'] not in ('RESOLVED','ESCALATED') and time.monotonic()<deadline:
                time.sleep(1)
                response=c.get('/v2/cases/'+case['id']);response.raise_for_status();case=response.json()
            assert case['status']==expected,(scenario,case)
            if expected=='RESOLVED':assert case['verification']['verified']
            else:assert case['escalation']['evidence_refs']
            assert all(a['idempotency_key'] for a in case['actions'])
            if scenario=='confirmation':
                response=c.post('/v2/cases/'+case['id']+'/replay');response.raise_for_status()
                assert response.json()['actions'][0]['id']==case['actions'][0]['id']
            entry={'scenario':scenario,'status':case['status'],'verified':case.get('verification',{}).get('verified',False),'seconds':round(time.monotonic()-started,2),'case_id':case['id'],'executor':case['workflow']['executor'],'n8n_execution_id':case['workflow']['n8n_execution_id'],'intake_provider':case['ai']['provider']}
            result.append(entry);print(json.dumps(entry),flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000');parser.add_argument('--output',default='tmp/resolve-smoke.json');args=parser.parse_args()
    results=run(args.url)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(results,indent=2))
