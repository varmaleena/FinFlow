"""Gemini transport with Vertex AI ADC (local login / Cloud Run service identity)."""
import os
import threading
from pathlib import Path
import httpx

_credentials = None
_lock = threading.Lock()

def provider():
    return 'vertex' if os.getenv('GOOGLE_GENAI_USE_VERTEXAI', '').lower() == 'true' else 'gemini'

def configured():
    return bool(os.getenv('GOOGLE_CLOUD_PROJECT')) if provider() == 'vertex' else bool(os.getenv('GEMINI_API_KEY'))

def model(): return os.getenv('GEMINI_MODEL', 'gemini-3.8-flash')

def vertex_headers():
    global _credentials
    try:
        import google.auth
        from google.auth.transport.requests import Request
        from google.auth.exceptions import GoogleAuthError
        class BoundedRequest(Request):
            def __call__(self, *args, **kwargs):
                kwargs['timeout'] = 10
                return super().__call__(*args, **kwargs)
        with _lock:
            if _credentials is None:
                # Avoid metadata probing delays on a developer machine without a login.
                adc = Path(os.getenv('APPDATA', str(Path.home()/'.config'))) / 'gcloud/application_default_credentials.json'
                if not os.getenv('K_SERVICE') and not os.getenv('GOOGLE_APPLICATION_CREDENTIALS') and not adc.exists():
                    raise ValueError('Vertex ADC sign-in required')
                _credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
            if not _credentials.valid: _credentials.refresh(BoundedRequest())
            return {'Authorization': 'Bearer '+_credentials.token, 'x-goog-user-project': os.environ['GOOGLE_CLOUD_PROJECT']}
    except ImportError as exc: raise ValueError('Install google-auth[requests] for Vertex AI') from exc
    except OSError as exc: raise ValueError('Vertex credentials are inaccessible') from exc
    except GoogleAuthError as exc: raise ValueError('Vertex authentication unavailable; renew ADC sign-in') from exc

def generate(payload, timeout=12):
    name = model()
    if provider() == 'vertex':
        project = os.environ.get('GOOGLE_CLOUD_PROJECT')
        if not project: raise ValueError('Vertex project is not configured')
        location = os.getenv('GOOGLE_CLOUD_LOCATION', 'global')
        host = 'aiplatform.googleapis.com' if location == 'global' else location+'-aiplatform.googleapis.com'
        url = f'https://{host}/v1/projects/{project}/locations/{location}/publishers/google/models/{name}:generateContent'
        headers = vertex_headers()
    else:
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent'
        headers = {'x-goog-api-key': os.environ.get('GEMINI_API_KEY', '')}
    response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response
