# Hermes plugin compatibility audit

Audit date: 2026-10-09. Scope: Magi's current draft [compatibility PR](https://github.com/MarcosAlves90/magi/pull/2), reviewed against the [Hermes plugin developer guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/index.md), [catalog submission policy](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/catalog-submission.md), [LLM access](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugin-llm-access.md), and native Hermes host code.

This document distinguishes implemented checks from upstream dependencies. A green validator is admission evidence for the code paths it scans, not proof of every live profile or race.

| Contract | Current implementation | Evidence / remaining uncertainty |
| --- | --- | --- |
| Manifest and LLM override consent | `plugin.yaml` declares `llm.provider_override` and `llm.model_override`; Stored memory exposes provider/model selectors populated from the active profile's configured Hermes model catalog. | Hermes admission validation passed at Magi commit `de3ca189`. Provider/model routing and profile-scoped persistence have fake/native-host tests; no external provider call was made. |
| No Hermes core runtime overrides | Magi no longer assigns a module alias in `sys.modules` and no longer replaces `MemoryStore._mutate`. | Source inspection and static Hermes validation. The backend still discovers the Agent-owned bridge by reading loaded module metadata; this is private host-layout coupling pending [Hermes #135903](https://github.com/NousResearch/hermes-agent/issues/135903). |
| Desktop SDK | Uses `@hermes/plugin-sdk`, registered routes/sidebar and `ctx.rest` for local backend requests. | UI contract tests and Hermes static validation passed; hands-on Desktop behavior across profiles has not been tested in this audit. |
| Host-owned LLM | Agent `register(ctx)` binds the per-profile plugin context; Desktop settings use its `get_config`/`set_config`, and compaction routes through its `ctx.llm` without chat-session or credential access. | Native-host integration test covers load, profile-scoped settings read/write, preview, unload, and reload, with a fake completion. Direct credential/provider calls were not tested. |
| Stored-memory atomicity | Backend calls `MemoryStore.apply_batch(..., expected_entries=reviewed_snapshot)`. The public Hermes implementation compares the ordered source under the existing file lock. | Native disk/race regression and two upstream conditional-batch tests in the Magi CI fork pin; [Hermes upstream PR #135901](https://github.com/NousResearch/hermes-agent/pull/135901) is awaiting review. |
| Older Hermes fail-closed behavior | If the host lacks the public `expected_entries` parameter, compaction Apply returns HTTP 409 without writing. | Unit regression confirms the unconditional API is never called. Other Magi features still support Hermes >=0.21.5. |
| User approval and privacy | Desktop approval/rejection goes through Hermes' pending-decision mechanism; stored-memory editing uses native MemoryStore; AI compaction requires explicit preview and Apply. | Unit/backend tests and source review. Preview content is sent to the user's Hermes-selected model per README disclosure. |

## Integration and validation evidence

- [Magi CI run 38012329440](https://github.com/MarcosAlves90/magi/actions/runs/38012329440): 85 tests passed, 96.05% line coverage, plugin admission validation passed at `de3ca189` (against Hermes fork commit `914df5f`).
- [Magi CI run 38012639732](https://github.com/MarcosAlves90/magi/actions/runs/38012639732), code commit `0e0622d9`: passed. Includes 2 native Hermes conditional-batch tests, 85 Magi tests, 96.05% line coverage and plugin validation. These results apply to the stated commit; later document-only commits require separate CI confirmation.
- Upstream Hermes PR Actions are currently `action_required`, requiring maintainer approval; no upstream CI pass is claimed.
- The prior local Hermes test result (81 passed, 95.99%) applies only to the earlier Magi `498920e` main commit, and does not validate this draft branch.

## Release conditions

1. The conditional memory API must be accepted in official Hermes and available in a published release; pin CI to an official immutable Hermes SHA instead of the temporary `MarcosAlves90/hermes-agent` fork.
2. Align `requires_hermes`, release notes and optional compaction availability with the released host interface, then run `./verify.sh` against that exact official version.
3. Test the installed plugin in Desktop with at least two independent profiles, verify provider/model selections stay isolated, and verify real consent-denied provider and model overrides; CI host tests use fakes and cannot establish end-to-end provider behaviour.
4. Resolve or receive a documented SDK ruling for the remaining bridge coupling in [Hermes #135903](https://github.com/NousResearch/hermes-agent/issues/135903) before asserting full, future-proof public-surface conformance.

Do not merge the draft Magi PR or claim catalog-ready compatibility while those release conditions are unverified.
