#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "[1/4] Python syntax"
python3 -m py_compile __init__.py core.py
if [[ -f dashboard/plugin_api.py ]]; then
  python3 -m py_compile dashboard/plugin_api.py
fi

echo "[2/4] Tests"
bash ci/test.sh

echo "[3/4] Coverage"
bash ci/coverage.sh
uv run --with 'coverage>=7,<8' python - <<'PY2'
import json
from pathlib import Path
import xml.etree.ElementTree as ET
root = ET.parse(Path('coverage.xml')).getroot()
line_rate = float(root.attrib['line-rate']) * 100
policy = json.loads(Path('.polis/policy.json').read_text())
gate = next(g for g in policy['gates'] if g['id'] == 'coverage')
if gate['operator'] != '>':
    raise SystemExit('unsupported coverage comparison')
threshold = float(gate['threshold_percent'])
print(f'production line coverage: {line_rate:.2f}% (required >{threshold:g}%)')
if not line_rate > threshold:
    raise SystemExit(f'production line coverage must be >{threshold:g}%')
PY2
rm -f .coverage coverage.xml

echo "[4/4] Hermes plugin validation"
hermes plugins validate "$ROOT" --install-deps
