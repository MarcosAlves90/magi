"""Contracts for the pinned, isolated Hermes compatibility CI matrix."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

REFS = {
    "minimum-0.21.5": ("v2026.9.24", "f97608f178d1ffeca59860195ab7da295f7c8e5f"),
    "ci-pinned": ("pinned-2026-10-08", "517b5e10febd619ce30bb22580e29b160266eb43"),
    "stable-0.22.5": ("v0.22.5", "bf0203a794a07aac3ff764972ab9087baa4e4f4e"),
}


def test_hermes_matrix_has_exact_isolated_release_refs():
    yaml = WORKFLOW.read_text()
    assert "matrix:" in yaml and "include:" in yaml
    assert "fail-fast: false" in yaml
    assert "continue-on-error: ${{ matrix.informational }}" in yaml
    assert "${{ matrix.hermes_sha }}" in yaml
    assert "${{ matrix.hermes_version }}" in yaml
    for label, (version, sha) in REFS.items():
        assert f"label: {label}" in yaml
        assert f"hermes_version: {version}" in yaml
        assert f"hermes_sha: {sha}" in yaml
    assert "refs/tags/v0.21.5" not in yaml  # This tag does not exist upstream.
    assert "git -C \"$hermes_dir\" rev-parse HEAD" in yaml
    assert "git -C \"$hermes_dir\" diff --quiet" in yaml
    assert "ci/hermes-compatibility.py" in yaml
    assert "./verify.sh" in yaml and "ci/desktop-test.sh" in yaml


def test_hermes_matrix_version_refs_are_full_immutable_shas():
    assert len({sha for _, sha in REFS.values()}) == 3
    assert all(re.fullmatch(r"[0-9a-f]{40}", sha) for _, sha in REFS.values())


def test_probe_script_has_explicit_memory_user_and_native_batch_checks():
    text = (ROOT / "ci" / "hermes-compatibility.py").read_text()
    assert 'for target in ("memory", "user")' in text
    assert "apply_batch" in text and "matched_entry" in text
    assert "TemporaryDirectory" in text

