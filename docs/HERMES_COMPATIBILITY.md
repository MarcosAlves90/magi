# Hermes plugin compatibility audit

Audit date: 2026-10-09. Scope: Magi's current draft [compatibility PR](https://github.com/MarcosAlves90/magi/pull/2), reviewed against the [Hermes plugin developer guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/index.md), [catalog submission policy](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/catalog-submission.md), [LLM access](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugin-llm-access.md), and native Hermes host code.

This document distinguishes implemented checks from upstream dependencies. A green validator is admission evidence for the code paths it scans, not proof of every live profile or race.

| Contract | Current implementation | Evidence / remaining uncertainty |
| --- | --- | --- |
| Manifest and LLM override consent | `plugin.yaml` declares `llm.provider_override` and `llm.model_override`; the native plugin settings form accepts a profile-scoped provider/model JSON object, not dynamic selectors. | Hermes admission validation passed at Magi commit `de3ca189`. Live profile routing checks are recorded below. |
| No Hermes core runtime overrides | Magi no longer assigns a module alias in `sys.modules` and no longer replaces `MemoryStore._mutate`. | Source inspection and static Hermes validation. The backend still discovers the Agent-owned bridge by reading loaded module metadata; this is private host-layout coupling pending [Hermes #135903](https://github.com/NousResearch/hermes-agent/issues/135903). |
| Desktop SDK | Uses `@hermes/plugin-sdk`, registered routes/sidebar and `ctx.rest` for local backend requests. | UI contract tests and Hermes static validation passed. The user reports that the Desktop interface was verified; the exact scenarios were not specified. |
| Host-owned LLM | Agent `register(ctx)` binds the per-profile plugin context; Hermes owns settings persistence, and compaction routes through its `ctx.llm` without chat-session or credential access. | Native-host integration tests use a fake completion; synthetic live requests through both profile bridges succeeded as recorded below. Magi did not read credentials directly. |
| Memory batch and concurrency | Magi checks the reviewed fingerprint before calling `MemoryStore.apply_batch(target, operations)` with exact entry pins; it does not pass `expected_entries` or fail closed when that parameter is unavailable. | A concurrent append or reordering between the pre-check and the locked batch may not be rejected. This limitation also exists in the catalog version and is documented in [Hermes memory compatibility](hermes-memory-compatibility.md). |
| Future conditional memory API | [Hermes PR #157035](https://github.com/NousResearch/hermes-agent/pull/157035) proposes an in-lock snapshot precondition; Magi does not currently use it. | CI tests the proposed Hermes behavior against its pinned PR revision; this does not establish that Magi applies the precondition. |
| User approval and privacy | Desktop approval/rejection goes through Hermes' pending-decision mechanism; stored-memory editing uses native MemoryStore; AI compaction requires explicit preview and Apply. | Unit/backend tests and source review. Preview content is sent to the user's Hermes-selected model per README disclosure. |

## Integration and validation evidence

- [Magi CI run 44868268943](https://github.com/MarcosAlves90/magi/actions/runs/44868268943) passed on commit `302816890b7d84a8997ab09cb14c34b276380d3d`: Hermes minimum-0.21.5, ci-pinned and stable-0.22.5, including Desktop interaction tests. GitGuardian also passed. The ci-pinned upstream conditional-batch tests do not mean Magi uses that precondition.
- [Magi CI run 38012329440](https://github.com/MarcosAlves90/magi/actions/runs/38012329440): 85 tests passed, 96.05% line coverage, plugin admission validation passed at `de3ca189` (against Hermes fork commit `914df5f`).
- [Magi CI run 38012639732](https://github.com/MarcosAlves90/magi/actions/runs/38012639732), code commit `0e0622d9`: passed. Includes 2 native Hermes conditional-batch tests, 85 Magi tests, 96.05% line coverage and plugin validation. These results apply to the stated commit; later document-only commits require separate CI confirmation.
- Upstream Hermes PR Actions are currently `action_required`, requiring maintainer approval; no upstream CI pass is claimed.
- The prior local Hermes test result (81 passed, 95.99%) applies only to the earlier Magi `498920e` main commit, and does not validate this draft branch.

### Live profile routing checks (2026-11-21 UTC)

These are manually recorded observations, not signed execution logs. Both profiles had Magi commit `682b60cfa18c19dbf67d1de7147f56d3f239b421` installed; the later CI commit above changes documentation only. With the owner's consent, provider/model override capabilities were granted in each profile and remained granted. Tests did not send stored memory or credentials in prompts.

- **Catalog and persistence:** each profile's saved provider/model was present in its own catalog. A different listed pair was saved through Hermes' native plugin settings writer, read back, resolved against that profile's catalog, and the original pair restored and read back. Empty, incomplete and unavailable pairs resolved to defaults. This tested the settings backend, not a Desktop UI interaction.
- **Magi bridge inference:** two successful rounds sent a synthetic structured request (`Return ok as true`) through each profile's existing Magi IPC bridge. `mariupol` returned `openai-codex` / `gpt-6.2-sol`; `hunter-douglas-2` returned `flow-ciandt` / `claude-sonnet-5-5`. In each successful response the reported provider/model matched the saved pair and the parsed JSON was `{"ok": true}`.
- **Observed failure:** the first hunter bridge request returned the generic `Magi Agent LLM request failed` error. A direct host request and two later bridge requests succeeded; the initial error's cause remains unknown. These successes do not prove future availability.
- **Not established:** the operator did not independently exercise the Desktop UI, cross-profile UI navigation, denied-consent behavior or real memory compaction. The user separately reported that the Desktop interface was verified, without specifying scenarios.

## Release conditions

1. The owner accepted the existing snapshot race for this version; an official release of the conditional API is not a prerequisite for retaining the current compaction behavior. Adopting that API remains a future hardening task.
2. The user reports that the Desktop interface was verified. Profile-scoped backend persistence and live bridge inference were checked as described above; cross-profile UI interaction and real consent-denied overrides were not independently tested.
3. Resolve or receive a documented SDK ruling for the remaining bridge coupling in [Hermes #135903](https://github.com/NousResearch/hermes-agent/issues/135903) before asserting full, future-proof public-surface conformance.

This audit alone does not establish catalog-ready or future-proof compatibility; the PR remains a draft pending its normal review and merge requirements.
