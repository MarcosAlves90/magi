# Hermes compatibility checks for Magi 1.7.8

The GitHub Actions matrix tests three **immutable Hermes commits** in separate
jobs. The minimum and existing pinned revision are required; the latest stable
release is initially informational (`continue-on-error`) so an upstream release
cannot silently change the previously reproducible admission gate. Every job
installs the exact Hermes revision, checks `git rev-parse HEAD`, and runs the
same Magi verification, Desktop interaction harness, and public memory API
probe. No Hermes source is edited.

## Selected versions and local evidence (2026-10-23)

| CI job | Hermes identity | Commit SHA | Python | Local install / native API probe |
| --- | --- | --- | --- | --- |
| `minimum-0.21.5` (required) | Release `v0.21.5`, Git tag **`v2026.9.24`** | `f97608f178d1ffeca59860195ab7da295f7c8e5f` | 3.13 | `uv sync --frozen --no-dev` PASS; `memory` and `user` add / pinned `apply_batch` / disk reload PASS |
| `ci-pinned` (required) | Prior pinned commit, 2026-10-08 | `517b5e10febd619ce30bb22580e29b160266eb43` | 3.14 | `uv sync --frozen --no-dev` PASS; both targets' native API probe PASS |
| `stable-0.22.5` (informational) | Published GitHub release `v0.22.5` (2026-10-23T05:28:45Z) | `bf0203a794a07aac3ff764972ab9087baa4e4f4e` | 3.14 | `uv sync --frozen --no-dev` PASS; both targets' native API probe PASS |

The original `v0.21.5` spelling is the Python package release number; GitHub
does **not** expose a `refs/tags/v0.21.5` ref. Its actual release commit is
`refs/tags/v2026.9.24^{}`. The oldest tested revision requires Python
`>=3.11,<3.14` in its `pyproject.toml`; attempting `uv sync --python 3.14`
failed for that reason. Its dedicated job uses Python 3.13. The two newer
revisions accept Python 3.14.

The local run used isolated, remote-fetched sparse checkouts outside the Magi
tree and clean virtual environments. An initial attempt to reuse the locally
installed Hermes Git objects failed because its partial clone lacked older
blobs; remote cloning resolved that checkout problem. A local full Magi
`verify.sh` run against the oldest revision initially failed on one
**Vitest mock DOM timing assertion** (`Save changes` disabled state,
`tests/desktop/magi.interaction.test.jsx`); the other six DOM tests passed,
and the native Hermes compatibility probe passed. The assertion was corrected
to await the asynchronously selected editor draft. POLIS validation for the
revised MAGI-014 contract then passed both enabled gates in isolated worktrees.
Because DOM tests mock Hermes entirely, the original failure was not evidence
of an upstream API incompatibility. The matrix's GitHub Actions results remain
unknown until the workflow actually runs.

## Operations checked

`python ci/hermes-compatibility.py` sets `HERMES_HOME` to a fresh temporary
directory **before importing Hermes** and refuses a profile-resolution
mismatch. It verifies `load_on_disk_store`, `apply_memory_pending`, the native
`MemoryStore.add`, one `MemoryStore.apply_batch` transaction with exact
`matched_entry` pins, and reload/persistence for **both** `memory` and `user`.
The operation inserts test-only synthetic strings and never touches the
developer's existing `MEMORY.md` or `USER.md`.

Separately, `./verify.sh` checks all Magi Python tests, line coverage of the
four production files, and `hermes plugins validate --install-deps`, while
`bash ci/desktop-test.sh` checks rendered Desktop behavior with an isolated SDK
mock. These checks do not prove every upstream API implementation remains
stable or that a complete end-to-end Hermes Desktop/Gateway session works.

To reproduce locally with a particular **already checked-out and installed**
Hermes revision (replace `<checkout>` with its absolute path), run:

```bash
PYTHONPATH="<checkout>" "<checkout>/.venv/bin/python" ci/hermes-compatibility.py
HERMES_HOME="$(mktemp -d)" PYTHONPATH="<checkout>" PATH="<checkout>/.venv/bin:$PATH" ./verify.sh
bash ci/desktop-test.sh
```

Confirm `git -C "<checkout>" rev-parse HEAD` against the SHA in the matrix
before testing. The run uses only a temporary Hermes profile, but installation
of dependencies needs package registry access and the isolated checkout needs
GitHub access. `HERMES_HOME` should point to a fresh, disposable directory.

The matrix provides sampled, repeatable compatibility evidence for the declared
`requires_hermes: ">=0.21.5"` range; it is not a guarantee for every
intermediate, prerelease, or future Hermes revision. Repository policy must be
revisited if a required version fails due to a confirmed upstream contract
change, rather than silently increasing the version floor.
