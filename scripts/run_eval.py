import subprocess
import sys
raise SystemExit(subprocess.call([sys.executable,'-m','pytest','apps/api/app/tests','-q']))
