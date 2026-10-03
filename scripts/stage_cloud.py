"""Stage only application source for Cloud Build. Never include local secrets or artifacts."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1];target=root/'tmp/cloud-source';target.mkdir(parents=True,exist_ok=True)
for folder in ['apps/api/app','apps/web/src','apps/web/public','data/seed']:
 shutil.copytree(root/folder,target/folder,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','tests','*.pyc'))
for file in ['requirements.txt','infra/Dockerfile','apps/web/package.json','apps/web/package-lock.json','apps/web/tsconfig.json','apps/web/vite.config.ts','apps/web/index.html']:
 (target/file).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/file,target/file)
print('Staged application source only:',target)
