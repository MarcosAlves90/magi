# Magi

Magi is a Hermes plugin for reviewing pending memory writes and managing the built-in `MEMORY.md` and `USER.md` stores in Hermes Desktop. Requires Hermes `>=0.21.5`.

**Current version:** 1.7.8

![Magi cover banner](docs/magi-cover-banner.png)

## Install

```bash
hermes plugins install https://github.com/MarcosAlves90/magi
hermes plugins enable magi
```

In Hermes Desktop, go to **Capabilities → Plugins** and enable both the **Agent** plugin for your active profile and the **Desktop Magi** plugin. These switches are independent. If Desktop was already open, choose **Rescan** first. Then open **Magi** from the sidebar.

## Use Magi in Desktop

### Pending writes

![Magi Pending writes: review queue, proposal details, and approval controls](docs/screenshots/pending-writes.png)

### Stored memory

![Magi Stored memory: memory budget, entry list, and editing controls](docs/screenshots/stored-memory.png)

- **Pending writes:** Review each proposal using **Overview**, **Proposal**, **Diff**, **Raw**, or **Verify**, then approve or reject it. Bulk actions require confirmation. Proposals targeting entries that no longer exist are marked obsolete and can be removed with **Delete obsolete**.
- **Stored memory:** Switch between **Memory** (`MEMORY.md`) and **User** (`USER.md`). Search entries, inspect usage and remaining capacity, and add or edit entries. Deletion requires confirmation.
- **Resizable views:** Drag the lower edge of the pending-write inspector or stored-memory editor to change its height.
- **AI compaction:** Select a store and choose **Compact with AI** to review a proposed shorter version. Magi accepts a preview only if it uses at most 60% of the original characters (at least a 40% reduction); it can retry up to three times. If no safe reduction is available, the memory stays unchanged. Nothing is written until you choose **Apply compaction**. Magi rejects a stale source at its initial fingerprint check and Hermes rejects missing or changed pinned entries under its batch lock. Concurrent additions may still be preserved; this is not a full-snapshot check under the lock.

Pending-write decisions and compaction previews work without an open chat session. Stored-memory changes use Hermes' native memory safeguards, including checks against stale data.

## Configuration

Set these options in the Magi plugin settings:

| Option | Default | Purpose |
| --- | --- | --- |
| `compaction_model` | Empty | Model for AI compaction; empty uses the active/default Hermes model. |
| `home_override` | Empty | Optional custom Hermes home directory; empty uses the active profile. |
| `default_page_size` | `20` | Default number of pending writes listed. |
| `max_page_size` | `100` | Maximum number of list/search results. |

Choosing a specific `compaction_model` requires Hermes permission `plugins.entries.magi.llm.allow_model_override` and compliance with any configured `allowed_models` restriction.

## Commands

In a Hermes session:

```text
/magi list [page] [limit]
/magi show <id|prefix|oldest|newest>
/magi diff <id|prefix|oldest|newest>
/magi raw <id|prefix|oldest|newest>
/magi find <text>
/magi stats
/magi verify [id|all]
/magi path <id|prefix>
/memory-show <id|prefix>
/memory-compact-preview <memory|user>
```

From the terminal:

```bash
hermes magi list
hermes magi show newest
hermes magi diff <id> | less
hermes magi raw <id> | less
hermes magi verify all
```

Use the terminal for large records that may exceed chat message limits.

## Security and privacy

- **Local reads:** Magi reads the active profile's pending-memory records and Hermes' `MEMORY.md` / `USER.md` stores. `home_override` can redirect these reads to a home directory you select.
- **Changes:** Approval, rejection, entry edits and compaction require explicit user actions. Writes go through Hermes' native memory APIs; Magi does not edit the stores directly.
- **AI access:** Compaction sends the selected memory content through Hermes' `ctx.llm` interface to the configured model/provider. Hermes owns provider credentials; Magi does not read or store them.
- **Other activity:** Magi makes no direct third-party network calls, launches no shell commands or subprocesses, runs no long-lived background jobs, and collects no telemetry or usage data. While its Desktop page is open, it polls the local plugin backend every five seconds.

## Development

Run the repository verification suite:

```bash
./verify.sh
```

This checks Python syntax, runs tests, enforces the **production line-coverage gate >90%**, and validates the plugin with Hermes' `plugins validate --install-deps` admission check. Run `bash ci/coverage.sh` independently to regenerate `coverage.xml` and print missing lines and branches for `__init__.py`, `core.py`, `runtime_bridge.py`, and `dashboard/plugin_api.py`. Tests under `tests/` are excluded from the production denominator. The report uses branch instrumentation, but the gate measures **lines**, not branches. On baseline `d9c72a8`, the old global XML report (including tests) measured **96.45%** line coverage; the production-only subset measured **774/846 = 91.49%** lines, a materially different metric. This figure describes exercised code, not the completeness of behavior testing or software security. Compaction apply calls Hermes' public `MemoryStore.apply_batch` and does not rebind its private `_mutate` method. See [Hermes memory compatibility and upstream dependencies](docs/hermes-memory-compatibility.md) for concurrency limits, pre-existing serialization behavior, and test evidence.

`verify.sh` invokes pytest **once**, inside `ci/coverage.sh`, and still propagates test and coverage failures. `bash ci/test.sh` remains available for test-only runs. On this macOS workspace, end-to-end `verify.sh` wall-clock time measured **38.66s** on `d9c72a8` (two test passes), **36.73s** after MAGI-013 (two passes), and **17.35s** after MAGI-020 (one pass). The observed final reduction against the original baseline is **21.31s (55.1%)**; individual runs are subject to machine load and warmed dependencies, so this is a measured comparison rather than a guaranteed speedup.

For the MAGI-012 Desktop interaction suite, use Node.js 24 or newer and run `npm ci --ignore-scripts --no-audit --no-fund` once, followed by `bash ci/desktop-test.sh` or `npm run test:desktop`. GitHub CI runs the Desktop interaction suite as a separate step, and `./verify.sh` retains its existing Python/Hermes checks. The Vitest/jsdom tests render the actual `desktop/plugin.js` with a minimal SDK `useQuery` implementation, and route all `ctx.rest` requests to an in-memory fixture. They exercise the current queue search, navigation, approval/rejection and bulk confirmation, network error/retry, stored add/edit/delete, stale compaction rejection, explicit preview/cancel/apply and keyboard Enter navigation. The tests do not use a real Hermes profile, browser, credentials, AI model or network during execution. **Coverage boundary:** pending decision partial-outcome UI, draft preservation on polling, editable compaction proposals and proactive fingerprint invalidation are separate features (MAGI-002/007/006/008); they are not asserted as implemented by this harness. Browser/Electron integrations and visual accessibility still need separate checks.
