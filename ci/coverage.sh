#!/usr/bin/env bash
set -euo pipefail
rm -f .coverage coverage.xml
uv run \
  --with 'pytest>=8,<10' \
  --with 'coverage>=7,<8' \
  --with 'fastapi>=0.115,<1' \
  --with 'httpx>=0.27,<1' \
  python -m coverage run --branch --source=. --omit='*/tests/*' -m pytest -q tests --rootdir=tests
production_files='__init__.py,core.py,runtime_bridge.py,dashboard/plugin_api.py'
uv run --with 'coverage>=7,<8' python -m coverage report -m --include="$production_files"
uv run --with 'coverage>=7,<8' python -m coverage xml --include="$production_files" -o coverage.xml
