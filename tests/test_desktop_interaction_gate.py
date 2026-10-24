"""Verify the real rendered Desktop test runner is shipped and executed."""

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_interaction_runner_is_available():
    runner = ROOT / "ci" / "desktop-test.sh"
    assert runner.is_file(), "Magi Desktop DOM interaction runner is missing"
    result = subprocess.run(
        ["bash", str(runner)],
        cwd=ROOT,
        env={**os.environ, "CI": "1", "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "Test Files" in result.stdout

