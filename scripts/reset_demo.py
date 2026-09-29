import os
import httpx
for merchant in ('m_001','m_002','m_003'):
    response = httpx.post('http://127.0.0.1:8000/v1/demo/reset', headers={'Authorization':'Bearer '+os.getenv('DEMO_API_TOKEN','finflow-local-demo'),'X-Merchant-Id':merchant})
    response.raise_for_status()
print('All three merchants reset.')
