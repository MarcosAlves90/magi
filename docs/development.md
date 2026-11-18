# Development and verification

This page is the contributor reference. The [README](../README.md) is the product overview; [documentation index](index.md) links to the user and security guides.

## Verify a checkout

From the repository root:

```bash
./verify.sh
```

The verification script checks Python syntax, runs the Python tests once, enforces the production line-coverage gate (**greater than 90%**), and runs Hermes' `plugins validate --install-deps` admission check. `bash ci/test.sh` is available for a test-only run. `bash ci/coverage.sh` independently regenerates `coverage.xml` and reports missed lines and branches for `__init__.py`, `core.py`, `runtime_bridge.py`, and `dashboard/plugin_api.py`.

Tests under `tests/` are excluded from the production denominator. Coverage data uses branch instrumentation, but the configured threshold is **lines, not branches**. On baseline `d9c72a8`, the old global XML report (including tests) measured 96.45%; the four production files measured 774/846 = 91.49%. Coverage measures exercised code, not behavioral completeness or security.

`verify.sh` invokes pytest once inside `ci/coverage.sh` and propagates both test and coverage failures. Historical macOS observations: 38.66s on `d9c72a8`, 36.73s after MAGI-013, and 17.35s after MAGI-020. The 21.31s / 55.1% comparison is a measured single-workspace result, not a guaranteed speedup.

## Desktop interaction tests

Use Node.js 24 or newer. Install the pinned development dependencies once and run the interaction suite:

```bash
npm ci --ignore-scripts --no-audit --no-fund
bash ci/desktop-test.sh
# or: npm run test:desktop
```

Vitest/jsdom renders the actual `desktop/plugin.js` with a minimal SDK `useQuery` implementation; `ctx.rest` requests go to an in-memory fixture. Tests exercise queue search/navigation/decisions and bulk confirmation, network error/retry, stored-memory CRUD, stale compaction rejection, preview/cancel/apply, and keyboard Enter navigation. They do not use a real Hermes profile, browser, credentials, AI model, or network.

The harness does not establish coverage for pending-decision partial-outcome UI, draft preservation during polling, editable compaction proposals, or proactive fingerprint invalidation (MAGI-002/007/006/008). Browser/Electron integration and visual accessibility require separate checks.

## Hermes compatibility

CI installs three immutable Hermes revisions in independent jobs and checks their exact commits. The minimum (`v2026.9.24`, Hermes 0.21.5, Python 3.13) and prior pinned revision (Python 3.14) are required; the selected latest stable (`v0.22.5`, Python 3.14) is informational. See the [compatibility matrix](hermes-compatibility-matrix.md) for commit SHAs, source/install evidence, reproduction commands, and limits.

`ci/hermes-compatibility.py` uses a fresh temporary profile before importing Hermes and tests native `MemoryStore` operations for both `memory` and `user`, including add, pinned `apply_batch`, disk reload, and persistence. It does not touch a developer's existing `MEMORY.md` or `USER.md`.

Magi compaction calls Hermes' public `MemoryStore.apply_batch`; it does not rebind the private `_mutate` method. The native batch protects its own write, but the public API does not provide a full-snapshot revision precondition under the lock. The [memory compatibility analysis](hermes-memory-compatibility.md) documents concurrency, identical-text ABA, serialization, crash/recovery evidence, and remaining upstream boundaries; those findings are not a claim that all interleavings or filesystems are proven safe.
