"""Live Vertex smoke check. Only synthetic complaint text is transmitted."""
from dotenv import load_dotenv
load_dotenv()
from apps.api.app.services.resolve_ai import extract
from apps.api.app.services.gemini_transport import provider, model

if __name__ == '__main__':
    value, meta = extract('My payment pay_confirmation has no merchant acknowledgement. Amount 2000 rupees.')
    print({'configured_provider': provider(), 'configured_model': model(), 'intake': value.model_dump(), 'actual_provider': meta})
    raise SystemExit(0 if not meta['fallback'] else 1)
