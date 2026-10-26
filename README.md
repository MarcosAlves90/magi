# Magi

Magi is a Hermes plugin for reviewing pending memory writes and managing the built-in `MEMORY.md` and `USER.md` stores in Hermes Desktop. Requires Hermes `>=0.21.5`.

**Current version:** 1.8.1

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

- **Pending writes:** Review each proposal using **Overview**, **Proposal**, **Diff**, **Raw**, or **Verify**, then approve or reject it. Search remains available; open **Filters** for action (`add`, `replace`, `remove`, `batch`), target store, origin, target status, and inclusive UTC creation-date filters. Active filters are counted; **Clear filters** restores the full queue. Invalid or missing dates display as **Unknown date** and do not match date ranges. The filtered count is shown alongside the total. Open **Actions for all pending items** to approve or reject the **entire queue**, including hidden matches; both actions require confirmation. Proposals targeting entries that no longer exist are marked obsolete and can be removed with **Delete obsolete**.
- **Stored memory:** Switch between **Memory** (`MEMORY.md`) and **User** (`USER.md`). Search entries, inspect the character budget and entry count, and add or edit entries. Character and token budgets are visible at a glance; the selected entry list and editor share a full-width responsive workspace. Deletion requires confirmation. Use **Inspect diagnostics** for each store to inspect exact entry counts, characters used/free, entry-size ranges, entries longer than 500 characters, and groups of exactly identical text. Use **View entry** to inspect a flagged entry. These are local, deterministic, informational indicators: long text is not necessarily poor quality, identical text does not detect semantic redundancy or contradictions, and diagnostics never edit data or call AI.

  While editing stored entries, unsaved drafts remain attached to the original entry and store when background polling refreshes. Navigation to another entry/store or to the other Magi section offers **Continue editing**, **Discard draft** or **Save and continue**. A changed source fingerprint blocks Save and preserves your text for manual reconciliation. Host navigation outside the plugin is controlled by Hermes and cannot be intercepted reliably; copy important unsaved text before leaving the host page.
- **Memory transfer:** Use the visible **Import JSON**, **Export memory/User**, and **Export both stores** actions. Exports may contain private information; Magi does not upload the file. Import accepts only this JSON format (up to 1 MB/1,000 entries), previews missing entries against the active character budget, and adds them only after confirmation. Existing entries are never overwritten. Each target is validated before writes, but multi-target writes are separate native batches and are not atomic together; concurrent changes can still race between preview and application.
- **History:** The **History** view keeps a profile-local audit of mutating actions initiated through Magi, with timestamps, target, outcome, and redacted error codes only. It never stores memory text, prompts, tokens, or raw exceptions. Records are bounded to 500 entries/256 KB under `plugin-data/magi` (directory `0700`, file `0600`). Date, operation, and outcome filters are visible inline; active filters are counted and can be reset. History can be cleared only after confirmation. This is not an undo log and excludes changes made directly in Hermes. If recording fails, Magi reports the audit limitation separately from the native operation result.
- **Resizable views:** Drag the lower edge of the pending-write inspector or stored-memory editor to change its height.
- **AI compaction:** In **Stored memory**, choose the visible **Compact with AI** action to review a proposed shorter version. In the preview, switch between **Entries** and **Diff** to inspect labeled unchanged, removed and added lines relative to the original snapshot sent to the AI; the snapshot and proposal are not modified by switching views. Magi accepts a preview only if it uses at most 60% of the original characters (at least a 40% reduction); it can retry up to three times. If no safe reduction is available, the memory stays unchanged. Nothing is written until you choose **Apply compaction**. Magi rejects a stale source at its initial fingerprint check and Hermes rejects missing or changed pinned entries under its batch lock. Concurrent additions may still be preserved; this is not a full-snapshot check under the lock.

  The preview observes the polled stored-memory fingerprint. If the source changes after preview creation, Magi shows **Preview outdated**, disables Apply and offers **Create a new preview**. This is an early UI warning; the backend still verifies the fingerprint when Apply is submitted, including intervening writes.

  You can select **Edit proposal** to edit text, split an entry at a newline/word boundary, merge adjacent entries, remove entries, or add a new one. Switching between Entries and Diff retains edits. The estimated character count, reduction and entry-count constraints update locally, while Hermes/Magi backend validation remains authoritative. An edited proposal requires a separate **Review edited proposal** step (which normalizes edge whitespace) followed by **Confirm edited compaction**. Manual changes are not semantically verified. Invalid or stale proposals are rejected without writing; **Cancel preview** discards the uncommitted proposal.
- **AI actions:** In **Stored memory**, the visible AI actions compact the selected store or run **Review memory with AI** for at most 20 source-linked suggestions about duplicates, groupings, possible contradictions, or long entries. The explicit review sends at most 100 entries/20,000 characters in one request to the active configured model. Each suggestion must cite exact text from every referenced entry; unsupported citations are discarded. Findings include uncertainty and are never applied automatically. Analysis errors and oversized stores leave memory unchanged.


  **Check preservation** is an optional experimental second-model review. It remains idle until you select it and separately confirm that the original and proposed content will be transmitted to the model/provider configured in Hermes; the existing `compaction_model` configuration and Hermes permission checks apply. Findings list suspected omissions, distortions or ambiguities with excerpts and explanations, and state uncertainty. They are hypotheses, not evidence that compacted memory is complete or safe. Failed reviews leave the original proposal and persisted memory unchanged; only **Apply compaction** can write.

  When generating a compaction, the status shows the request and elapsed time without inventing a completion percentage. **Stop waiting** abandons the frontend wait and ignores delayed responses; this does **not** cancel the request already running in Hermes or at the provider, guarantee fewer billed tokens, or undo an operation. You can request a fresh preview manually. Network errors, apparent timeouts and validated failures have different feedback; only confirmed successful Apply writes a compacted memory.

Pending-write decisions and compaction previews work without an open chat session. Stored-memory changes use Hermes' native memory safeguards, including checks against stale data.

Decision responses report `outcome: complete | partial | failed`, counts (`attempted`, `applied`, `rejected`, `failed`) and per-record `items[]` with status and errors. A partial batch can return HTTP 200 because the request completed at the transport layer; `success` is true only when every selected decision and its pending-file cleanup completed. If a memory write was applied but the proposal file could not be removed, its status is `applied_cleanup_failed`: inspect and reconcile it before considering any retry. The legacy `output` text is retained.

Before approval, Magi validates each pending proposal's structure and checks destructive `matched_entry` pins against the currently readable target. Malformed, missing and unverifiable proposals cannot be approved, but may be rejected. **Approve all** attempts individually eligible records and reports blocked records in the partial result. Checks in Magi are preconditions before the native Hermes operation, not an atomic snapshot guarantee; concurrent changes can still cause a native rejection.

If a pending proposal remains after its memory mutation succeeds, Magi stores a content-free, profile-local marker under `plugins/magi/reconciliation/` and blocks subsequent approvals of that exact proposal. Already-stored pending additions are marked ambiguous even without a marker. Inspect Stored memory before explicitly rejecting such a pending proposal. This is a conservative recovery safeguard: a process crash between the Hermes write and Magi's marker is still possible, and raw modifications by other writers may be indistinguishable from a previous approval. Magi does not guarantee exactly-once execution across these independent stores.

After a bulk or individual decision, expand **Decision results** to inspect each record's ID, final status and safe failure summary. Separate counts show applied, rejected, failed and reconciliation-required records. **Open pending record** navigates to a still-pending item; **Retry rejection** is available only when a previous rejection failed and its proposal still exists. Magi never automatically retries approvals or entries marked `applied_cleanup_failed`. The result panel and confirmation feedback remain when switching between Pending writes and Stored memory.

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
- **Audit history:** Only metadata for mutating actions routed through Magi is stored locally under the active profile. It is bounded and permission-restricted; external Hermes activity is not attributed to Magi.
- **AI access:** Compaction and the explicit **Review memory with AI** action send the selected memory content through Hermes' `ctx.llm` interface to the configured model/provider. These actions are never automatic; review uses one bounded request and does not modify the store. Hermes owns provider credentials; Magi does not read or store them.
- **Other activity:** Magi makes no direct third-party network calls, launches no shell commands or subprocesses, and collects no telemetry or usage data. While the Agent plugin is loaded, it exposes a private Unix-domain socket under the active profile's `plugin-data/magi` directory so the Dashboard API can reach Hermes' `ctx.llm` across process boundaries. On unload—and if startup fails after the serving thread begins—the listener stops accepting requests and waits for in-flight structured requests to finish before releasing `ctx.llm`; this drain has no fixed deadline, so a provider call that never returns can keep unload or cleanup waiting indefinitely; the plugin does not read credentials or log prompts. A permission-restricted `.magi-llm-ipc.lock` in the active profile root coordinates startup and unload across plugin processes and contains no request data. The socket is limited to the owning OS user and the active profile path; local processes running as that same user are within the trust boundary (Hermes profiles are not OS-level isolation boundaries). While its Desktop page is open, Magi polls the local plugin backend every five seconds. Memory export downloads are assembled in the browser as local JSON data URLs and are never sent to an export service.

## Development

Run the repository verification suite:

```bash
./verify.sh
```

This checks Python syntax, runs tests, enforces the **production line-coverage gate >90%**, and validates the plugin with Hermes' `plugins validate --install-deps` admission check. Run `bash ci/coverage.sh` independently to regenerate `coverage.xml` and print missing lines and branches for `__init__.py`, `core.py`, `runtime_bridge.py`, and `dashboard/plugin_api.py`. Tests under `tests/` are excluded from the production denominator. The report uses branch instrumentation, but the gate measures **lines**, not branches. On baseline `d9c72a8`, the old global XML report (including tests) measured **96.45%** line coverage; the production-only subset measured **774/846 = 91.49%** lines, a materially different metric. This figure describes exercised code, not the completeness of behavior testing or software security. Compaction apply calls Hermes' public `MemoryStore.apply_batch` and does not rebind its private `_mutate` method. See [Hermes memory compatibility and upstream dependencies](docs/hermes-memory-compatibility.md) for concurrency limits, pre-existing serialization behavior, and test evidence.

`verify.sh` invokes pytest **once**, inside `ci/coverage.sh`, and still propagates test and coverage failures. `bash ci/test.sh` remains available for test-only runs. On this macOS workspace, end-to-end `verify.sh` wall-clock time measured **38.66s** on `d9c72a8` (two test passes), **36.73s** after MAGI-013 (two passes), and **17.35s** after MAGI-020 (one pass). The observed final reduction against the original baseline is **21.31s (55.1%)**; individual runs are subject to machine load and warmed dependencies, so this is a measured comparison rather than a guaranteed speedup.

For the MAGI-012 Desktop interaction suite, use Node.js 24 or newer and run `npm ci --ignore-scripts --no-audit --no-fund` once, followed by `bash ci/desktop-test.sh` or `npm run test:desktop`. GitHub CI runs the Desktop interaction suite as a separate step, and `./verify.sh` retains its existing Python/Hermes checks. The Vitest/jsdom tests render the actual `desktop/plugin.js` with a minimal SDK `useQuery` implementation, and route all `ctx.rest` requests to an in-memory fixture. They exercise the current queue search, navigation, approval/rejection and bulk confirmation, network error/retry, stored add/edit/delete, stale compaction rejection, explicit preview/cancel/apply and keyboard Enter navigation. The tests do not use a real Hermes profile, browser, credentials, AI model or network during execution. **Coverage boundary:** pending decision partial-outcome UI, draft preservation on polling, editable compaction proposals and proactive fingerprint invalidation are separate features (MAGI-002/007/006/008); they are not asserted as implemented by this harness. Browser/Electron integrations and visual accessibility still need separate checks.

The [Hermes compatibility matrix](docs/hermes-compatibility-matrix.md) uses
three exact source commits in independent CI jobs: the `0.21.5` release
(`v2026.9.24` tag, Python 3.13), the prior pinned commit (Python 3.14), and
the latest stable release selected on 2026-10-23 (`v0.22.5`, Python 3.14).
The first two are required gates; latest stable is initially informational.
It checks native, temporary-profile `MemoryStore` transactions on both targets,
the existing Python/Hermes validation and the deterministic Desktop suite.
