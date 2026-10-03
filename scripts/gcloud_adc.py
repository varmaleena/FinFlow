"""Run gcloud using the user's ADC login without printing or persisting access tokens."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from apps.api.app.services.gemini_transport import vertex_headers

def main():
    path=shutil.which('gcloud.cmd') or shutil.which('gcloud')
    if not path:
        candidate=Path(os.environ.get('LOCALAPPDATA',''))/'Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd'
        if candidate.exists():path=str(candidate)
    if not path:raise SystemExit('Google Cloud SDK not found')
    env=os.environ.copy()
    env['CLOUDSDK_AUTH_ACCESS_TOKEN']=vertex_headers()['Authorization'].removeprefix('Bearer ')
    return subprocess.run([path,*sys.argv[1:]],env=env).returncode
if __name__=='__main__':raise SystemExit(main())
