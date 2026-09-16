import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config/roles.json').read_text())

def secret_values():
    bundled = ROOT / 'secrets.env'
    path = Path(os.getenv('DEVFLOW_SECRETS_FILE', str(bundled if bundled.exists() else '/Users/joaopaulo/Documents/secret.key')))
    values = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values
