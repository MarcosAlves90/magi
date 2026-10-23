"""Behavioral regressions for the repository's local quality scripts."""

import json
import os
from pathlib import Path
import shutil
import subprocess


REPO = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = {
    "__init__.py",
    "core.py",
    "runtime_bridge.py",
    "dashboard/plugin_api.py",
}


def _sandbox(tmp_path):
    project = tmp_path / "project"
    (project / "ci").mkdir(parents=True)
    (project / "dashboard").mkdir()
    for relative in ("verify.sh", "ci/coverage.sh", "ci/test.sh", ".polis/policy.json"):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    for relative in PRODUCTION_FILES:
        (project / relative).touch()

    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['QUALITY_INVOCATIONS'], 'a') as log:\n"
        "    log.write(json.dumps(args) + '\\n')\n"
        "if 'pytest' in args and os.environ.get('QUALITY_FAIL_PYTEST') == '1':\n"
        "    sys.exit(7)\n"
        "if args[-2:] == ['python', '-']:\n"
        "    import subprocess\n"
        "    sys.exit(subprocess.call([sys.executable, '-'], stdin=sys.stdin))\n"
        "if 'xml' in args:\n"
        "    rate = os.environ.get('QUALITY_LINE_RATE', '0.9149')\n"
        "    pathlib.Path('coverage.xml').write_text('<coverage line-rate=\"' + rate + '\"/>')\n"
    )
    uv.chmod(0o755)
    hermes = binary / "hermes"
    hermes.write_text("#!/bin/sh\nexit 0\n")
    hermes.chmod(0o755)
    log = tmp_path / "invocations.jsonl"
    env = os.environ.copy()
    env.update({"PATH": f"{binary}{os.pathsep}{env['PATH']}", "QUALITY_INVOCATIONS": str(log)})
    return project, log, env


def _invocations(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_production_coverage_command_scope(tmp_path):
    project, log, env = _sandbox(tmp_path)
    subprocess.run(["bash", "ci/coverage.sh"], cwd=project, env=env, check=True)
    invocations = _invocations(log)
    runs = [args for args in invocations if "coverage" in args and args[args.index("coverage") + 1] == "run"]
    xmls = [args for args in invocations if "coverage" in args and args[args.index("coverage") + 1] == "xml"]
    reports = [args for args in invocations if "coverage" in args and args[args.index("coverage") + 1] == "report"]
    assert len(runs) == len(xmls) == len(reports) == 1
    assert "--branch" in runs[0]
    assert any(arg.startswith("--omit=") and "tests" in arg for arg in runs[0])
    for args in (xmls[0], reports[0]):
        includes = [arg.split("=", 1)[1] for arg in args if arg.startswith("--include=")]
        assert len(includes) == 1
        assert set(includes[0].split(",")) == PRODUCTION_FILES


def test_production_coverage_gate_is_explicit_and_consistent():
    policy = json.loads((REPO / ".polis/policy.json").read_text())
    gate = next(gate for gate in policy["gates"] if gate["id"] == "coverage")
    assert gate["report"] == "coverage.xml"
    assert gate["threshold_percent"] == 90
    assert gate["operator"] == ">"


def test_verify_runs_test_suite_once(tmp_path):
    project, log, env = _sandbox(tmp_path)
    result = subprocess.run(["bash", "verify.sh"], cwd=project, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    pytest_calls = [args for args in _invocations(log) if "pytest" in args]
    assert len(pytest_calls) == 1, f"pytest ran {len(pytest_calls)} times: {pytest_calls}"
    assert "Hermes plugin validation" in result.stdout
    assert "production line coverage: 91.49%" in result.stdout


def test_verify_fails_on_production_coverage_below_threshold(tmp_path):
    project, _, env = _sandbox(tmp_path)
    env["QUALITY_LINE_RATE"] = "0.9000"
    result = subprocess.run(["bash", "verify.sh"], cwd=project, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "production line coverage must be >90%" in result.stderr


def test_verify_propagates_pytest_failures(tmp_path):
    project, log, env = _sandbox(tmp_path)
    env["QUALITY_FAIL_PYTEST"] = "1"
    result = subprocess.run(["bash", "verify.sh"], cwd=project, env=env, capture_output=True, text=True)
    assert result.returncode == 7
    assert "pytest" in _invocations(log)[0]
