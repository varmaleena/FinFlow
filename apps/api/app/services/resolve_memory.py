"""Advisory context only. This module has no access to financial-state or action writes."""
import json
import os
from pathlib import Path
import httpx

PLAYBOOKS = json.loads((Path(__file__).resolve().parents[4]/'data/seed/playbooks.json').read_text())
DATASET = 'paytm_resolve_support_v1'

def search_context(intent, canonical_state):
    # Only enum-derived topic text is transmitted; never customer complaints or ledger data.
    topic = {'SETTLEMENT':'settlement capture reconciliation missing evidence', 'HIGH_RISK':'human review financial action blocked'}.get(intent, 'payment merchant notification acknowledgement pending finality')
    query = f'{topic}. State category: {canonical_state}. Explain the support procedure, not the status of any specific payment.'
    words = set(query.lower().split())
    ranked = sorted(PLAYBOOKS,key=lambda p:len(words.intersection(p['text'].lower().split())),reverse=True)[:2]
    result = {'provider':'local_playbooks','authoritative':False,'boundary':'Advisory context only. Payment Twin and policy remain authoritative.', 'results':ranked,'query':query,'dataset':DATASET}
    key = os.getenv('COGNEE_API_KEY')
    if not key or os.getenv('COGNEE_ENABLED','false').lower() != 'true': return result
    try:
        response = httpx.post(os.getenv('COGNEE_BASE_URL','https://api.cognee.ai').rstrip('/')+'/api/v1/search',
            headers={'X-Api-Key':key},timeout=15,
            json={'query':query,'searchType':'GRAPH_COMPLETION','datasets':[DATASET],'onlyContext':True,'topK':3,'includeReferences':True,'sessionId':'paytm-resolve-support-v1'})
        response.raise_for_status()
        data=response.json()
        # Provider content is displayed as untrusted advisory text, never fed to decision logic.
        if not data: return {**result,'fallback_reason':'Cognee returned no context'}
        context = data if isinstance(data,str) else json.dumps(data,ensure_ascii=False)
        return {**result,'provider':'cognee','context':context[:6000]}
    except (httpx.HTTPError,ValueError,TypeError):
        return {**result,'fallback_reason':'Cognee unavailable; local support playbooks retained'}
