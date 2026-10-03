"""Optional retrieval only. Memory never determines payment state or permissions."""
import json
import os
import asyncio
from pathlib import Path

PLAYBOOKS = json.loads((Path(__file__).resolve().parents[4] / 'data/seed/playbooks.json').read_text())

async def seed_memory():
    import cognee
    for item in PLAYBOOKS:
        await cognee.add(item['id'] + ': ' + item['text'], dataset_name='finflow_policies')
    await cognee.cognify()

async def search_memory(query):
    if os.getenv('COGNEE_ENABLED','false').lower() == 'true' and os.getenv('COGNEE_API_KEY'):
        from .resolve_memory import search_context
        return await asyncio.to_thread(search_context, 'SETTLEMENT' if 'settlement' in query.lower() else 'PAYMENT', 'UNKNOWN')
    if os.getenv('COGNEE_LOCAL_ENABLED','false').lower() == 'true':
        try:
            import cognee
            result = await asyncio.wait_for(cognee.search(query_text=query, datasets=['finflow_policies']), timeout=8)
            return {'provider':'cognee','context':str(result),'authoritative':False}
        except (ImportError, asyncio.TimeoutError): pass
    words = set(query.lower().split())
    matches = sorted(PLAYBOOKS, key=lambda p:len(words.intersection(p['text'].lower().split())), reverse=True)[:2]
    return {'provider':'local_playbooks','results':matches,'authoritative':False}
