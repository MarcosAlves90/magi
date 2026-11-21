# Hermes plugin compatibility audit

Audit date: 2026-10-09. Scope: Magi's current draft [compatibility PR](https://github.com/MarcosAlves90/magi/pull/2), reviewed against the [Hermes plugin developer guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/index.md), [catalog submission policy](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/catalog-submission.md), [LLM access](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugin-llm-access.md), and native Hermes host code.

This document distinguishes implemented checks from upstream dependencies. A green validator is admission evidence for the code paths it scans, not proof of every live profile or race.

| Contract | Current implementation | Evidence / remaining uncertainty |
| --- | --- | --- |
| Manifest and LLM override consent | `plugin.yaml` declares `llm.provider_override` and `llm.model_override`; the native plugin settings form accepts a profile-scoped provider/model JSON object, not dynamic selectors. | Hermes admission validation passed at Magi commit `de3ca189`. Provider/model routing has fake/native-host tests; no external provider call was made. |
| No Hermes core runtime overrides | Magi no longer assigns a module alias in `sys.modules` and no longer replaces `MemoryStore._mutate`. | Source inspection and static Hermes validation. The backend still discovers the Agent-owned bridge by reading loaded module metadata; this is private host-layout coupling pending [Hermes #135903](https://github.com/NousResearch/hermes-agent/issues/135903). |
| Desktop SDK | Uses `@hermes/plugin-sdk`, registered routes/sidebar and `ctx.rest` for local backend requests. | UI contract tests and Hermes static validation passed. The user reports that the Desktop interface was verified; the exact scenarios were not specified. |
| Host-owned LLM | Agent `register(ctx)` binds the per-profile plugin context; Hermes owns settings persistence, and compaction routes through its `ctx.llm` without chat-session or credential access. | Native-host integration tests use a fake completion. Direct credential/provider calls were not tested. |
| Memory batch and concurrency | Magi checks the reviewed fingerprint before calling `MemoryStore.apply_batch(target, operations)` with exact entry pins; it does not pass `expected_entries` or fail closed when that parameter is unavailable. | A concurrent append or reordering between the pre-check and the locked batch may not be rejected. This limitation also exists in the catalog version and is documented in [Hermes memory compatibility](hermes-memory-compatibility.md). |
| Future conditional memory API | [Hermes PR #157035](https://github.com/NousResearch/hermes-agent/pull/157035) proposes an in-lock snapshot precondition; Magi does not currently use it. | CI tests the proposed Hermes behavior against its pinned PR revision; this does not establish that Magi applies the precondition. |
| User approval and privacy | Desktop approval/rejection goes through Hermes' pending-decision mechanism; stored-memory editing uses native MemoryStore; AI compaction requires explicit preview and Apply. | Unit/backend tests and source review. Preview content is sent to the user's Hermes-selected model per README disclosure. |

## Integration and validation evidence

- [Magi CI run 38012329440](https://github.com/MarcosAlves90/magi/actions/runs/38012329440): 85 tests passed, 96.05% line coverage, plugin admission validation passed at `de3ca189` (against Hermes fork commit `914df5f`).
- [Magi CI run 38012639732](https://github.com/MarcosAlves90/magi/actions/runs/38012639732), code commit `0e0622d9`: passed. Includes 2 native Hermes conditional-batch tests, 85 Magi tests, 96.05% line coverage and plugin validation. These results apply to the stated commit; later document-only commits require separate CI confirmation.
- Upstream Hermes PR Actions are currently `action_required`, requiring maintainer approval; no upstream CI pass is claimed.
- The prior local Hermes test result (81 passed, 95.99%) applies only to the earlier Magi `498920e` main commit, and does not validate this draft branch.

## Release conditions

1. The owner accepted the existing snapshot race for this version; an official release of the conditional API is not a prerequisite for retaining the current compaction behavior. Adopting that API remains a future hardening task.
2. The user reports that the Desktop interface was verified. Cross-profile persistence and real consent-denied provider/model overrides have not been independently verified in this audit; CI host tests use fakes.
3. Resolve or receive a documented SDK ruling for the remaining bridge coupling in [Hermes #135903](https://github.com/NousResearch/hermes-agent/issues/135903) before asserting full, future-proof public-surface conformance.

This audit alone does not establish catalog-ready or future-proof compatibility; the PR remains a draft pending its normal review and merge requirements.
