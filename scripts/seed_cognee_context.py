"""Opt-in indexing of bundled, synthetic support playbooks only."""
import json
import os
from pathlib import Path
import httpx
from dotenv import load_dotenv
load_dotenv()
from apps.api.app.services.resolve_memory import PLAYBOOKS, DATASET

if __name__=='__main__':
    key=os.environ.get('COGNEE_API_KEY')
    if not key:raise SystemExit('Set COGNEE_API_KEY in .env first. No documents were sent.')
    base=os.getenv('COGNEE_BASE_URL','https://api.cognee.ai').rstrip('/')
    content='\n\n'.join(p['id']+': '+p['text'] for p in PLAYBOOKS)
    with httpx.Client(base_url=base,headers={'X-Api-Key':key},timeout=60) as c:
        existing=c.get('/api/v1/datasets/');existing.raise_for_status()
        present=any(d.get('name')==DATASET for d in existing.json())
        if present:
            print('Support dataset already exists; reusing it without uploading duplicate documents.')
            added=existing
        else:
            added=c.post('/api/v1/add',files={'data':('paytm-resolve-support.txt',content,'text/plain')},data={'datasetName':DATASET});added.raise_for_status()
        processed=c.post('/api/v1/cognify',json={'datasets':[DATASET],'runInBackground':True});processed.raise_for_status()
        print({'dataset':DATASET,'add_status':added.status_code,'cognify_status':processed.status_code,'note':'Processing may continue asynchronously. No customer complaints or payment events uploaded.'})
