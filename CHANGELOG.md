# Changelog

## Unreleased

## 1.8.4 — 2026-10-30

- Preserve Hermes' active profile context for LLM requests through the profile-local IPC bridge, ensuring profile-scoped AI actions resolve against the intended context.

## 1.8.3 — 2026-10-30

- Render pending-record details from the already-loaded record, avoiding repeated queue reads while preserving response content.
- Reorganized the documentation into focused guides, updated the workflow diagram, and refreshed the synthetic Desktop screenshots including History.

## 1.8.2 — 2026-10-29

- Require explicit `llm.model_override` consent for compaction-model overrides; hosts without capability probing fail closed while default-model requests remain available.
- Version the profile-local IPC bridge as protocol v2 and reject active legacy listeners that cannot enforce the consent gate. Preserve Dashboard's case-insensitive search behavior while avoiding Hermes' inline shell-scan spelling.

## 1.8.1 — 2026-10-26

- Redesigned the Desktop workspace with a full-width memory list/editor, always-visible usage estimates and common actions, responsive review panes, inline history filters, and explicit accessible confirmations. Existing memory, transfer, AI, pending-write, and audit safeguards remain intact.
- Restored Review memory with AI and Compact with AI across Dashboard/Agent processes through a profile-local Unix socket to the active Agent's `ctx.llm`. The bridge bounds and validates structured requests, stops accepting work on unload, and drains in-flight calls before releasing context; Hermes configuration remains unchanged.

## 1.8.0 — 2026-10-23

- Simplified the Desktop workspace with progressive disclosures for advanced
  pending/history filters, bulk decisions, usage details, memory transfer and AI tools.
  Clarified action labels and accessible tooltips, keyboard focus, active filter
  counts and filter reset; existing import/export, AI review and history filters
  remain available.
- MAGI-011: pending bulk decisions now show per-record results in a collapsible,
  accessible panel with separate applied, rejected, failed and reconciliation
  counters. Failed records still in the queue can be opened without replaying
  mutations; only failed rejection attempts offer a manual retry. Result state
  and confirmation feedback persist when changing the Magi section.
- MAGI-004: after a native pending approval, Magi records a content-free,
  profile-scoped reconciliation marker before removing the pending file.
  Uncertain cleanup prevents repeat approval across backend reloads; duplicate
  pending additions already present in memory are also flagged as ambiguous.
  Rejection clears the pending record and marker without replaying a write.
  The journal is best-effort across crashes and does not provide exactly-once
  atomicity between Hermes memory and Magi's proposal queue.
- MAGI-003: pending proposals that are malformed, missing destructive
  `matched_entry` pins or otherwise unverifiable are rejected before native
  approval. Memory-store read errors disable approval; bulk operations still
  attempt other valid records and return per-record results. Rejection remains
  available; final concurrency conflicts continue to be decided by Hermes.
- MAGI-002: pending approve/reject now reports complete, partial or failed
  outcomes with per-record statuses and counts. Successful memory mutations
  followed by pending-file cleanup failure are distinguished from completed
  operations; Desktop displays partial outcomes as warnings. The legacy text
  output remains available; `success` is false for incomplete decisions.
- MAGI-001: rejected Magi-controlled additions, replacements, pending approvals
  and AI-compaction proposals containing Hermes' complete entry delimiter
  (`\\n§\\n`), before native mutation. Invalid model proposals retry safely;
  existing malformed entries are flagged by read-only index diagnostics.
  Ordinary multiline and Unicode entries remain supported.
- Preserved dirty stored-memory drafts during polling and guarded in-plugin
  selection/section navigation with explicit continue, discard or save actions;
  source changes disable Save until manually reconciled (MAGI-007).
- Allowed manual editing, splitting, merging and removing compacted entries,
  with local estimates, duplicate/delimiter checks, backend-enforced budget
  and fingerprint preconditions, and explicit final confirmation (MAGI-006).
- Exposed a per-store source fingerprint in the existing memory response, and
  disabled applying visibly outdated compaction previews with an explicit
  regenerate action (MAGI-008). Native backend stale checks remain authoritative.
- Added a selectable line diff between the original compaction snapshot and
  proposed entries, with explicit unchanged/removed/added labels and no extra
  AI request (MAGI-005).
- Added per-store, read-only memory diagnostics with exact duplicate-text
  groups, character usage, length distribution, and links to flagged entries.
  Informational thresholds and limitations are documented; the indicators
  neither invoke AI nor trigger automatic edits (MAGI-018).
- Added combinable pending-queue search, action, store, origin, status and UTC
  creation-date filters, with an explicit clear action and filtered/global
  totals. Invalid dates are labeled unknown; bulk approval/rejection remains
  explicitly scoped to the entire queue, even with filters active (MAGI-010).
- Added an explicitly authorized, optional AI preservation review of compaction
  snapshots with quoted evidence, omission/distortion/ambiguity suspicions and
  uncertainty labeling. Review failures never apply or alter proposals (MAGI-009).
- Added elapsed-time feedback and an explicit "Stop waiting" action for
  long-running compaction requests. Abandoning only ignores the late Desktop
  response; it does not cancel backend work or imply that token costs stop (MAGI-019).

- MAGI-017: added an explicit, read-only AI organization analysis for stored
  memory. Suggestions cite exact original text, expose uncertainty, discard
  invalid references, and make one bounded request without applying changes.
- MAGI-016: added a profile-local, metadata-only history for mutating Magi
  actions, with bounded retention, restricted file permissions, date/operation/
  outcome filters, confirmed clearing, and explicit warnings when audit writes
  fail. History is not an undo log and does not include external Hermes changes.
- MAGI-015: added local versioned JSON export and a bounded import preview
  for Memory, User, or both. Imports validate schema, duplicates, Hermes entry
  delimiters and character budgets, then add only missing entries through
  Hermes' native batch API after explicit confirmation. Per-target partial
  results and concurrency/atomicity limits are documented.
- MAGI-014: introduced isolated Hermes compatibility CI jobs pinned to exact
  SHAs for the 0.21.5 minimum (`v2026.9.24`, Python 3.13), the existing pinned
  revision and latest stable `v0.22.5`; the two baseline jobs are required and
  the latest is informational. Added a disposable-profile native Memory/User
  transaction probe, local-version evidence, and documented known limits.

- MAGI-012: added deterministic React DOM interaction tests for the actual
  Desktop module using Hermes' Vitest/React Testing Library patterns and a
  mock SDK/REST context; covers queue decisions, errors, navigation, stored
  memory CRUD, and compaction preview/cancel/apply. Added CI and local runner,
  explicit keyboard navigation assertions, and documentation of outstanding
  interaction features beyond this test-only change.
- MAGI-020: removed the duplicate pytest pass from `verify.sh`, leaving one
  coverage-instrumented test run while preserving standalone `ci/test.sh`,
  production coverage enforcement, syntax checks, and Hermes plugin admission.
  Added shell regressions for test invocation count and failure propagation.
  On the measured macOS baseline, verification improved from 38.66s to 17.35s
  (single-run observation; elapsed time depends on the environment).
- MAGI-013: measured coverage exclusively for the four production Python
  modules, with branch instrumentation and per-file missed-line reporting.
  Set the POLIS and local verification line-coverage threshold to the measured,
  achievable >90% production level; documented the former misleading 96.45%
  global metric versus 91.49% production on baseline `d9c72a8`.
- Documented the Hermes upstream API guarantees still needed for atomic
  snapshot/version preconditions, including reordering and ABA scenarios;
  distinguished the pre-existing shared-delimiter serialization issue from
  regressions caused by the public batch API migration, and summarized the two
  isolated stress campaigns and their remaining uncertainties.
- Applied Desktop AI compaction through Hermes' public `MemoryStore.apply_batch`
  without rebinding the private `_mutate` method or depending on `skip_drift`,
  resolving the Hermes plugin catalog rule 9 review.
- Kept the preview fingerprint precheck, exact `matched_entry` pins, and native
  all-or-nothing batch writes for both Memory and User stores. Concurrent
  additions remain intact, while missing pinned entries reject the batch;
  full-snapshot revalidation under the lock requires a future Hermes API
  precondition parameter.
- Added focused regressions for public-API-only compaction, concurrent add and
  remove behavior, and disk-backed Hermes transactions, with the original
  1.7.8 manifest, preview limits, and other plugin capabilities unchanged.

## 1.7.8 — 2026-10-17

- Removed the process-global module alias from the LLM context bridge and
  resolved the active Agent bridge by its plugin file, rejecting ambiguous or
  unavailable contexts.
- Reorganized Stored memory metrics into compact Entries, Estimated tokens,
  and Characters summaries above a full-width Character budget bar, and
  consolidated search, Memory/User selection, and actions into one responsive
  toolbar.
- Clarified the selected state of both Memory/User and Pending writes/Stored
  memory switches with a shared visual treatment, distinct hover/focus states,
  and accessible pressed-state semantics.
- Added focused regression coverage for the compact layout, navigation,
  responsive controls, and selection states.
- Renamed the visible workspace heading to **Magi Workspace**, added a small
  decorative wand icon to **Compact with AI**, and updated the README with the
  current version and separate Pending writes / Stored memory screenshots.
- Removed six generated POLIS `.polis` delivery packages from Git tracking and
  ignored future packages across the repository, while keeping the POLIS
  retention configuration, contracts, plans, proofs, and validation evidence
  available for version control without rewriting published history.

## 1.7.7 — 2026-10-08

- Closed a compaction race where another Hermes writer could add a memory entry
  after Magi checked the preview fingerprint but before native `apply_batch`
  reloaded the disk under its exclusive lock. Stale previews now abort within
  that locked mutation before any new compacted entries are written.
- Added regression coverage for an intervening writer, plus disk-backed native
  Hermes integration proving a rejected stale preview preserves every entry
  and a refreshed preview commits successfully.

## 1.7.6 — 2026-10-08

- Removed the model-facing `maxItems` schema constraint so Hermes' own
  structured-output validation cannot abort Magi's retry on an overlong list;
  Magi still enforces the entry-count cap after parsing.
- Reused the same 60%-of-original hard character budget on all three passes,
  eliminating the late-pass 55% reduction requirement that rejected previously
  acceptable proposals. Preferred budgets still tighten on retries.
- Retried Hermes schema errors and malformed structured responses up to three
  attempts. Invalid candidates never replace the next retry's source; failures
  leave stored memory untouched.
- Added regression tests for overlong output, retry consistency, Hermes schema
  exceptions, malformed output, and safe failure without Apply.

## 1.7.5 — 2026-10-07

- Made the entry-content viewer vertically resizable in both Pending writes and
  Stored memory, with bounded default/minimum/maximum heights and internal
  scrolling preserved.
- Stored-memory add/edit textareas now use the same resize behavior as pending
  Overview and technical inspection views, without changing backend contracts.

## 1.7.4 — 2026-10-07

- Added a `compaction_model` Magi setting for choosing the model used by AI
  compaction previews; leaving it empty preserves Hermes' active/default model.
- Routed the configured model through Hermes' supported `ctx.llm` facade for both
  Agent commands and Desktop preview requests, while preserving Hermes'
  `allow_model_override` and model-allowlist trust gates.
- Added regression coverage for configured-model routing on both compaction entry
  points.

## 1.7.3 — 2026-10-07

- Made AI compaction more aggressive without lowering its semantic-safety bar:
  three passes now progressively remove prose scaffolding, repeated subject
  wrappers, inferable consequences, and other lexical overhead while keeping the
  final 55% minimum-reduction acceptance threshold.
- Added an explicit structured `cannot_compact_further` signal. Magi challenges
  early claims, accepts the signal only on the final pass, and then reports a
  non-error "no change" result with the model's reason instead of presenting a
  failed budget check as an unexplained compaction error.
- Retry passes now keep working from the shortest candidate seen so far rather
  than allowing a later, more verbose proposal to become the new baseline.

## 1.7.2 — 2026-10-07

- Fixed multi-profile Desktop requests so pending-memory review resolves the
  same request-scoped Hermes home as the native MemoryStore via
  `hermes_constants.get_hermes_home()`.
- Added regression coverage proving a context-local profile override lists,
  approves, and rejects only that profile's staged memory records even when
  process-level `HERMES_HOME` points elsewhere.

## 1.7.1 — 2026-10-06

- Added catalog-ready compatibility metadata with `requires_hermes: ">=0.21.5"`.
- Added and documented the disclosures required by Hermes catalog rule 13.
- Added a clean screenshot captured from the real Magi Desktop UI for catalog
  and pinned README rendering.

## 1.7.0 — 2026-10-06

- Renamed the plugin end-to-end to **Magi**, including plugin/dashboard/Desktop IDs, route, slash command, CLI command, runtime bridge alias, tests, documentation, and repository URL.
- Replaced the Hermes sidebar icon with the built-in `wand` Codicon.
- Removed the previous branded slash/CLI aliases so Hermes configuration and navigation use one canonical plugin identity.

## 1.6.0 — 2026-10-06

- Redesigned the Desktop dashboard around two clear workflows: reviewing pending proposals and maintaining stored memory, with stronger hierarchy, contextual actions, responsive layouts, and clearer empty/loading/error states.
- Added lightweight reactive polish using native CSS/Tailwind transitions, short fade-ins, progress animation, and loading pulses with reduced-motion support; no animation dependency was added.
- Pending review now exposes queue health metrics, filtered-result counts, status-aware proposal cards, a clearer decision header, and progressive Overview/Proposal/Diff/Raw/Verify inspection.
- Stored memory now exposes budget metrics and a live usage bar, clearer Memory/User switching, search result counts, richer entry cards, unsaved/stale editor states, character counts, and a structured AI-compaction review panel.

## 1.5.10 — 2026-10-06

- Pending-write inspection text is now selectable in **Overview**, **Proposal**, **Diff**, **Raw**, and **Verify**, so standard drag-selection plus copy shortcuts work in Hermes Desktop.
- Selection is enabled only on the inspection content, leaving tabs and action controls non-selectable.

## 1.5.9 — 2026-10-06

- Fixed pending-memory **Approve**, **Reject**, **Approve all**, **Reject all**, and **Delete obsolete** actions requiring an open or focused Hermes chat session.
- Desktop decisions now call the profile-scoped plugin backend instead of `slash.exec`; approved writes still replay through Hermes' native `apply_memory_pending()` pinned-entry semantics, while rejected proposals are removed from the pending queue.
- Added regression coverage proving pending decisions work without `activeSessionId` or `focusedSessionId`.

## 1.5.8 — 2026-10-06

- Pending replace/remove proposals now compare their pinned `matched_entry` targets with the current stored Memory/User entries and are marked obsolete when those targets no longer exist.
- Desktop highlights obsolete proposals in the pending list and detail view, disables approval that Hermes would reject as stale, and relabels Reject as **Delete obsolete** so the dead proposal can be removed through Hermes' native decision path.
- Batch proposals are marked obsolete when any pinned destructive operation targets a missing entry; legacy destructive proposals without a pin are shown as unverifiable and cannot be approved.

## 1.5.7 — 2026-10-06

- Fixed the retry pass re-reading the full original corpus after a too-large first proposal, which encouraged the model to reconstruct the same verbose memory again.
- The second pass now rewrites the first-pass candidate directly under the stricter budget and is explicitly forbidden from reintroducing details already discarded by the first pass.
- Added regression coverage proving that retry input is the previous proposal rather than the original source corpus.

## 1.5.6 — 2026-10-06

- Fixed compaction accepting outputs that violated the stricter character budget stated in the prompt; each attempt now enforces its advertised hard budget in plugin code.
- Moved the invariant compaction policy into Hermes' supported `system_prompt` channel so stored memory is lower-authority data and source entry boundaries cannot steer the output structure.
- Explicitly directs the model to optimize for the fewest coherent thematic entries, including one output entry consolidating many source entries when appropriate.
- Increased the minimum first-pass reduction from 25% to the prompt's actual 40% hard budget; the retry remains stricter at a 55% reduction target.

## 1.5.5 — 2026-10-06

- Fixed AI compaction accepting proposals that exceeded the requested entry-count limit when Hermes/provider-side JSON Schema validation was unavailable or not enforced.
- Added plugin-side entry-count validation: over-count proposals automatically retry once with the exact violation called out, then fail closed if the model still exceeds the limit.
- Added **Add entry** for both `MEMORY.md` and `USER.md`, delegating validation and persistence to Hermes `MemoryStore.add`.
- Added exact-entry deletion for both targets through `MemoryStore.remove`, with a two-step **Delete entry** / **Confirm delete** Desktop flow.
- Added regression coverage for providers that ignore `maxItems`, add/delete success, stale/native-store failures, and the new Desktop controls.

## 1.5.4 — 2026-10-06

- Fixed the AI compaction review layout so it is height-bounded, keeps its controls visible, scrolls proposed entries internally, and cannot cover the stored-memory editor below it.
- Changed compaction to treat the source as one memory corpus instead of preserving entry boundaries, with a dynamic schema cap that targets substantially fewer output entries.
- Reworked the compaction prompt around the durable, actionable core and explicitly removes examples, explanations, narrative history, temporary state, repeated qualifiers, and low-value nuance.
- Require at least 25% exact-character reduction before a proposal is accepted, with substantially tighter first-pass and retry budgets.
- Added preview metadata for source/output entry counts and actual percentage reduction.

## 1.5.3 — 2026-10-06

- Added visible in-progress feedback for AI compaction with an activity indicator and live elapsed time while Hermes is generating and validating the preview.
- Strengthened the compaction prompt with an explicit character budget, tighter compression guidance, and a preference for fewer entry-boundary overheads without dropping unique information.
- Added one automatic retry with a stricter target when the first AI proposal does not reduce the exact stored-memory character footprint.
- Changed compaction acceptance to the exact character footprint Hermes enforces; token counts remain informational estimates.

## 1.5.2 — 2026-10-06

- Fixed **Compact with AI** so preview generation no longer requires an open, active, or focused Hermes chat session.
- Moved Desktop preview requests to the plugin REST backend while preserving Hermes `ctx.llm` ownership of default-model routing, credentials, trust checks, and attribution.
- Kept preview generation non-mutating and retained stale-fingerprint validation plus one atomic `MemoryStore.apply_batch()` transaction for explicit application.

## 1.5.1 — 2026-10-06

- Fixed Stored memory token estimates to use Hermes' documented memory-budget scale of 2.75 chars/token instead of the generic model token estimator.
- Added exact character usage/limit and percentage used for both `memory` and `user`, using the active profile's configured MemoryStore limits.
- Aligned AI compaction before/after token estimates with the same Hermes memory-budget scale.

## 1.5.0 — 2026-10-06

- Added approximate token usage for both stored `memory` and `user` targets using Hermes' `estimate_tokens_rough` helper.
- Added **Compact with AI** using Hermes `ctx.llm` with the active/default provider and model, with no provider/model override.
- Added preview-first compaction with before/after token estimates, model attribution, proposed entries, and explicit **Apply compaction** / **Cancel preview** controls.
- Added stale-source fingerprint protection and atomic whole-target application through one `MemoryStore.apply_batch()` transaction.
- Added regression coverage for token metadata, default-model LLM routing, preview/apply separation, stale conflicts, and batch failures without real-memory mutation.

## 1.4.0 — 2026-10-05

- Added a top-level switch between pending writes and stored memory.
- Added Memory and User views that list every built-in `MEMORY.md` and `USER.md` entry with search and refresh.
- Added direct editing of existing stored entries from Desktop.
- Added structured stored-memory GET/PUT backend routes that delegate replacements to Hermes `MemoryStore` with the exact original entry pinned for stale-write protection.
- Preserved all v1.3 pending-review controls and native `/memory approve|reject` decision paths.

## 1.3.0 — 2026-10-05

- Added a formatted Overview as the default record view, with readable metadata and proposed content/change blocks.
- Added individual Approve and Reject controls backed by Hermes' documented `slash.exec` `/memory` command path.
- Added Approve all and Reject all with an explicit confirmation step before bulk mutation.
- Kept the plugin backend GET-only; Hermes core remains authoritative for applying or discarding staged memory writes.
- Added structured payload data to the detail endpoint for the Overview renderer.

## 1.2.0 — 2026-10-05

- Added a native Hermes Desktop **Magi** page and sidebar entry.
- Added profile-scoped, read-only backend routes for pending record lists and complete Proposal/Diff/Raw/Verify views.
- Added search, manual refresh, five-second polling fallback, empty/error states, and explicit backend-disabled guidance.
- Kept all memory mutations in Hermes core; the plugin backend exposes GET routes only.
- Added strict POLIS Red→Green coverage for the unified Desktop package and backend API.

## 1.1.0 — 2026-10-05

- Prepared and validated as a planned POLIS V6.10.0 behavior-preserving release.
- Added strict root-level validation with 24 tests and Cobertura coverage gate >95%.
- Fixed distribution verification so tests run from the package root.
- Added explicit POLIS validation contract and consumer handoff guidance.
- Runtime plugin implementation remains unchanged from 1.0.0.

## 1.0.0 — 2026-10-05

- `/magi` complete read-only inspector.
- `/memory-show` convenience shortcut.
- Terminal `hermes magi` surface.
- Full payload rendering, unified diffs, search, stats and validation.
- Exact/unique-prefix/oldest/newest selectors.
- Pagination.
- ANSI/control/bidi escaping.
- Profile-aware HERMES_HOME resolution with explicit override.
- Installer, uninstaller, verification script and unit tests.
