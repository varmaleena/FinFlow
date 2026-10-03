"""All test entry points use an isolated database and never call paid providers."""
import os
import tempfile
import pytest

_directory = tempfile.TemporaryDirectory(prefix='paytm-resolve-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + _directory.name.replace('\\', '/') + '/test.db'
os.environ['MONITOR_INTERVAL'] = '3600'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'false'
os.environ['GEMINI_API_KEY'] = ''
os.environ['LLM_API_KEY'] = ''
os.environ['RESOLVE_N8N_WEBHOOK_URL'] = ''
os.environ['RESOLVE_IDENTITIES'] = ''
os.environ['RESOLVE_DEMO_MODE'] = 'true'
os.environ['COGNEE_ENABLED'] = 'false'
os.environ['COGNEE_API_KEY'] = ''

@pytest.fixture(scope='session', autouse=True)
def close_test_database():
    yield
    from apps.api.app.models import engine
    engine.dispose()
    _directory.cleanup()
