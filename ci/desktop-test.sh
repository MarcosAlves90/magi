#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x node_modules/.bin/vitest ]]; then
  echo 'Installing lockfile-pinned Desktop test tooling'
  npm ci --ignore-scripts --no-audit --no-fund --prefer-offline
fi
npm run test:desktop
