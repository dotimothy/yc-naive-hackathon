#!/usr/bin/env python3
"""Interactive secret entry; credential never appears in shell argv/history."""
import getpass
from pathlib import Path
import re
import subprocess
import sys

cli = Path(__file__).resolve().parents[1] / 'node_modules/.bin/naive'
key = getpass.getpass('Naive API key (hidden): ')
result = subprocess.run([str(cli), 'login'], input=key + '\n', text=True, capture_output=True)
output = (result.stdout + result.stderr).replace(key, '[REDACTED]')
print(re.sub(r'sk_(?:live|test)_[A-Za-z0-9_.-]+', '[REDACTED]', output), end='')
sys.exit(result.returncode)
