"""Provision only Paytm Resolve app secrets and its database/user. Never print values."""
import argparse
import base64
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote
import httpx
from dotenv import load_dotenv
load_dotenv()
from apps.api.app.services.gemini_transport import vertex_headers
PROJECT='spry-catcher-509805-u4'
ACCOUNT=f'paytm-resolve@{PROJECT}.iam.gserviceaccount.com'
INSTANCE='paytm-resolve-db'
LOCAL=Path('tmp/resolve-cloud-access.json')

def access():
    if LOCAL.exists():return json.loads(LOCAL.read_text())
    value={'project':PROJECT,'database_password':secrets.token_urlsafe(32),'workflow_secret':secrets.token_urlsafe(40),
        'operations_token':secrets.token_urlsafe(32),'merchant_token':secrets.token_urlsafe(32),'customer_token':secrets.token_urlsafe(32)}
    LOCAL.parent.mkdir(parents=True,exist_ok=True);LOCAL.write_text(json.dumps(value,indent=2))
    return value

def checked(response):
    if not response.is_success:
        raise RuntimeError(f'Cloud request failed: HTTP {response.status_code}; credential values suppressed')
    return response.json() if response.content else {}

def provision_secrets(client,values):
    identities={values['operations_token']:{'role':'operations','merchant_id':'m_001'},values['merchant_token']:{'role':'merchant','merchant_id':'m_001'},values['customer_token']:{'role':'customer','merchant_id':'m_001','customer_id':'customer_001'}}
    password=quote(values['database_password'],safe='')
    payloads={'resolve-database-url':f'postgresql+psycopg://resolve_app:{password}@/resolve?host=/cloudsql/{PROJECT}:asia-south1:{INSTANCE}',
        'resolve-identities':json.dumps(identities),'resolve-workflow-secret':values['workflow_secret']}
    if os.getenv('COGNEE_API_KEY'):payloads['resolve-cognee-key']=os.environ['COGNEE_API_KEY']
    for name,value in payloads.items():
        url=f'https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/{name}'
        current=client.get(url)
        if current.status_code==404:
            checked(client.post(f'https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets',params={'secretId':name},json={'replication':{'automatic':{}}}))
            checked(client.post(url+':addVersion',json={'payload':{'data':base64.b64encode(value.encode()).decode()}}))
        else:
            checked(current)
            existing=checked(client.get(url+'/versions/latest:access'))
            if base64.b64decode(existing['payload']['data']).decode()!=value:raise RuntimeError(f'{name} already has another value; not overwriting')
        policy=checked(client.get(url+':getIamPolicy'))
        bindings=policy.setdefault('bindings',[])
        binding=next((b for b in bindings if b['role']=='roles/secretmanager.secretAccessor' and not b.get('condition')),None)
        if binding is None:binding={'role':'roles/secretmanager.secretAccessor','members':[]};bindings.append(binding)
        member='serviceAccount:'+ACCOUNT
        if member not in binding['members']:binding['members'].append(member)
        checked(client.post(url+':setIamPolicy',json={'policy':policy}))
        print('Ready:',name,flush=True)

def provision_database(client,values):
    base=f'https://sqladmin.googleapis.com/sql/v1beta4/projects/{PROJECT}/instances/{INSTANCE}'
    status=checked(client.get(base))['state']
    if status!='RUNNABLE':raise RuntimeError('Database not ready: '+status)
    databases=checked(client.get(base+'/databases')).get('items',[])
    if not any(d['name']=='resolve' for d in databases):
        op=checked(client.post(base+'/databases',json={'name':'resolve'}));print('Database creation operation:',op.get('name'))
        return False
    users=checked(client.get(base+'/users')).get('items',[])
    if not any(u['name']=='resolve_app' for u in users):
        op=checked(client.post(base+'/users',json={'name':'resolve_app','password':values['database_password']}));print('Database user creation operation:',op.get('name'))
        return False
    print('Database and application user are ready.');return True

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('step',choices=['secrets','database']);args=parser.parse_args()
    values=access()
    with httpx.Client(headers=vertex_headers(),timeout=30) as client:
        if args.step=='secrets':provision_secrets(client,values)
        else:provision_database(client,values)
