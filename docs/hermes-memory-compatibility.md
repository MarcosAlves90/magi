# Hermes memory compatibility: Magi 1.7.8

This note records the compatibility boundaries identified while addressing
[Hermes catalog PR #140369](https://github.com/NousResearch/hermes-agent/pull/140369),
review `5557895958`. It distinguishes the behavior of Magi's compaction route
from the guarantees provided by Hermes' `MemoryStore`. **No upstream defect fix
is claimed, and these findings are not new regressions attributed to the
`9327744` change.** Both `MEMORY.md` (`memory`) and `USER.md` (`user`) were tested.

## Implementation and guarantees that remain in place

- Magi's `dashboard/plugin_api.py::apply_memory_compaction` checks the preview's
  ordered source fingerprint **before** the batch, validates the proposed entry
  count and character budget, then calls the public
  `store.apply_batch(target, operations)`. It does **not** rebind/call
  `MemoryStore._mutate` or use its private `skip_drift` argument.
- Magi constructs exact `matched_entry` pins for every source removal, followed
  by additions of the compacted content. Hermes reloads the target under its
  file lock, validates the batch against a working copy, checks its final
  budget, and publishes a complete result if all operations succeed. Under
  the normal atomic-rename path exercised by these tests, disk readers see
  an old or new complete file. An absent/changed pin rejects the batch
  without writing partial results.
- Additions by a cooperating concurrent writer, between the fingerprint check
  and Hermes' locked batch, are preserved even when compaction succeeds. Two
  ordinary competing compactions on the same source yield one success and one
  stale-pin rejection once the first replaces the source. These guarantees
  concern **each batch**, not a global snapshot transaction spanning preview
  and apply or a group of unrelated pending-record decisions.

The existing [POLIS V6 contract](../.polis/artifacts/contracts/sha256-ca265600c38af73a67d0d7192896982fc381bf34c6ca783cfc2c83a15536ae90.json)
expressly acknowledges this boundary in `REQ-3`. The historical 1.7.7
CHANGELOG entry describes the former in-lock fingerprint guard; the 1.7.8
entry supersedes that implementation. The review fix is recorded in
`93277443fff21d4b97355d833cd544b5e2d25947`, with the contract and
CHANGELOG committed in `030df6ebd87b0c001b35a638fe51c2710ac15c7e`.

## Confirmed limitations requiring an upstream API change for stronger guarantees

### 1. No public atomic full-snapshot precondition

**Classification:** known compatibility limitation introduced as an accepted
trade-off when the plugin stopped intercepting a private method; **not** a
failure of Hermes' documented `apply_batch` atomicity.

**Reproduction:** preview `[A, B]`, retain its fingerprint, and pause apply
after the HTTP precheck but before `apply_batch`. Another Hermes writer appends
`C`. The batch can remove the still-present pinned `A` and `B`, add summary
`P`, and return HTTP 200 with `[C, P]`. If the writer instead removes or changes
`A`, the pinned batch returns HTTP 409 and leaves the writer's changes intact.
With the former private wrapper, the append also caused HTTP 409 because it
rechecked the entire fingerprint inside Hermes' lock.

**Reordering:** in the same window, remove and re-add `A`, obtaining `[B, A]`.
All pinned values still exist, so the public batch can commit `P` even though
the ordered snapshot differs. The former wrapper rejected that changed order.
The current behavior respects `REQ-3`, but **a successful apply does not prove
that the full reviewed snapshot remained unchanged**.

**Upstream requirement:** expose an optional, backward-compatible expected
snapshot/revision precondition on the public batch API, tested under the same
exclusive lock as the reload and write. On mismatch it should return a
distinguishable conflict without persisting anything; Hermes should expose a
snapshot or revision to obtain alongside the preview. A post-write or
plugin-side check is not an equivalent atomic guarantee.

### 2. `matched_entry` pins identify text, not an entry incarnation (ABA)

**Classification:** known limitation of the public pin contract, not a
newly introduced bug in Hermes persistence or batch atomicity.

**Reproduction:** request T2 has already passed the preview fingerprint check
for `[A, B]`. Another batch replaces `[A, B]` with `[P]`; a writer later adds
exactly `A` and `B` again, giving `[P, A, B]`. T2 can remove those reintroduced
values and add `Q`, leaving `[P, Q]`, because its pins match text. In the
second campaign this was reproduced for both targets. Duplicate removes of
the same pin within one batch, without a re-add in between, correctly fail
atomically; Magi's normal compaction path does not generate duplicate pins.

**Upstream requirement:** if callers need to reject ABA even when *all* content
returns to identical bytes, comparing only an expected fingerprint is
insufficient. Hermes would need a monotonically changing store revision or
stable entry identities, exposed when reading and atomically checked during
`apply_batch`. Revision tracking must cover every cooperating writer; raw
external edits that bypass the lock cannot inherit these guarantees.

## Confirmed pre-existing serialization issue: shared responsibility

**Classification:** pre-existing round-trip defect/validation gap, **not** a
regression caused by replacing `_mutate` with `apply_batch`, and **not
exclusively upstream**.

Hermes' `tools/memory_tool_store.py` defines `ENTRY_DELIMITER = "\n§\n"`.
Its `_write_file` joins entries using that delimiter and `_parse_entries`
splits on the same bytes. A candidate such as the single string
`"brief\n§\nanother"` can therefore be stored as *two* entries upon reload.
Magi's compaction endpoint checks the incoming list length and uniqueness,
but does not reject an embedded delimiter before calling `apply_batch`. The
HTTP result can report one entry while a newly loaded store reports two;
entry-count limits measured before serialization may no longer match disk.

This behavior was reproduced for `memory` and `user` in the **first** campaign
(`edge-faults.log`: two reproductions). Inspection of Magi at the historical
`65e94bd` baseline and Hermes at its CI-pinned revision confirmed both sides
of the behavior preceded `9327744`.

**Ownership:** Hermes owns the shared serialization format and any general
lossless escaping, round-trip validation or structured storage migration for
all clients. **Magi can independently reject an embedded delimiter in its own
input** as a narrower defense; other Hermes writers would remain
affected without an upstream fix. Neither change has been implemented as part
of the catalog-review correction. Avoid treating the issue as proof that the
public batch API itself violates its all-or-nothing disk-write guarantee.

## Crash, error and recovery evidence

- An isolated process killed **before** atomic `rename`, or a simulated
  pre-rename I/O failure, normally left the prior disk file intact. After a
  successful `rename`, killing the process or injecting a later error left a
  complete **new** file despite the missing HTTP acknowledgement. A caller
  must reload the source and treat retries as potentially stale, not infer
  failure of the commit from a broken response.
- One **initial uninstrumented** pilot asserted unexpectedly: in a
  `before_rename_kill` test for `user` (iteration 7), a fresh store reload
  reported the compacted value instead of the expected old value. The original
  log contains neither a disk-byte dump nor the child's publication trace,
  so it cannot establish why the injection and observed state disagreed.
  Later **180 instrumented** fault cases across both
  targets and six modes, plus **120 additional pre-rename SIGKILL** cases,
  passed. **Root cause unresolved**; no reproducible Magi or Hermes bug is
  established. Do not label the initial observation "fixed", or dismiss it
  as definitively a test-harness error.

## Evidence and verification boundaries

The two executed campaigns used isolated temporary memory directories and
the real disk-backed Hermes store, not users' live memories:

| Evidence | Observed result |
| --- | --- |
| First campaign: Magi complete suite | 91/91 PASS |
| First campaign: native stress; multiprocess races | 4,800/4,800 scenarios; 200/200 races PASS |
| Second campaign: deterministic interleavings and twin compactions | 2,000/2,000 PASS |
| Second campaign: 8/16/24 concurrent add-writers and two compactors | 540/540 races; 8,640 confirmed writes preserved; 1,080 compaction attempts rejected under contention |
| Second campaign: reordering, ABA and duplicate-pin scenarios | 600/600 PASS (including reproduction of the documented limitations) |
| Second campaign: Hermes CI-pinned revision | 480/480 deterministic cases; 19/19 selected native upstream tests PASS |
| Fault-injection evidence | 180 instrumented cases + 120 extra pre-rename kills PASS; one earlier inconclusive pilot |
| First campaign: POLIS and Hermes validator | `workspace validate` / `test.complete` / `coverage` PASS; Hermes validator 15/15 checks PASS, with pre-existing private-import warnings |

The corresponding **local-only**, non-versioned harnesses and logs reside in
`../polis-feature/review-140369/campaign-20261017/` and
`../polis-feature/review-140369/campaign2-20261017/` relative to the Magi
checkout. They are *not* part of this repository; the table records observed
results rather than claiming that those logs are available to every clone.

The original coverage gate measured **96.45% global line coverage** (2,062 /
2,138), **including test files**. The four main production files measured
**91.49%** (774 / 846), and `dashboard/plugin_api.py` measured **83.21%**
(228 / 274). These are not production-only 95% results. The second campaign
added external targeted tests but did not establish a new combined coverage
percentage; no increase is claimed.

The existing POLIS V6 contract is a locked record of the earlier source fix;
its `scope.allowed_paths` did not include this follow-up documentation file.
Its previous workspace validation is not a fresh approval of this additional
documentation, and its historical local-only delivery restrictions do not
describe the publication scope of later, separately authorized work.

**Remaining boundaries:** test clients and simulated interleavings do not
prove interactive Desktop UI behavior or all OS/filesystem failure modes.
Hermes' locked batch guarantees only apply to cooperating writers using its
lock; uncoordinated raw file edits are outside that transaction boundary.
Hermes' `utils.atomic_replace` has platform/filesystem-specific fallbacks for
some rename errors; the campaign did not establish equivalent crash-atomic
behavior for those fallback paths. The inconclusive pilot does **not** prove
that a fallback was used.
The catalog review does not authorize private API interception as a workaround.
