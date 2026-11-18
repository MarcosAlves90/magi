"""Executable pending-queue filter behavior and scope contracts."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


SOURCE = (Path(__file__).resolve().parents[1] / "desktop/plugin.js").read_text(encoding="utf-8")


def filter_records(records, filters):
    assert "function filterPendingRecords(" in SOURCE
    if shutil.which("node") is None:
        pytest.skip("Node.js required for executable Desktop filter regressions")
    script = r"""
const vm = require('node:vm')
let source = require('node:fs').readFileSync('desktop/plugin.js', 'utf8')
const start = source.indexOf('function pendingCreatedDate(')
const end = source.indexOf('const CONTROL_TRANSITION', start)
if (start < 0 || end < 0) throw Error('Pending filter helper unavailable')
const {records, filters} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'))
const result = vm.runInNewContext(source.slice(start, end) + '\nfilterPendingRecords(records, filters)', {records, filters})
process.stdout.write(JSON.stringify(result))
"""
    result = subprocess.run(
        ["node", "-e", script], input=json.dumps({"records": records, "filters": filters}),
        text=True, capture_output=True, check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    return json.loads(result.stdout)


def test_pending_filters_intersection():
    records = [
        {"id": "one", "action": "add", "target": "memory", "origin": "foreground", "summary": "keep", "created_at": 1767225600, "target_status": {"state": "ready"}},
        {"id": "two", "action": "add", "target": "user", "origin": "foreground", "summary": "keep", "created_at": 1767225600, "target_status": {"state": "ready"}},
        {"id": "three", "action": "remove", "target": "memory", "origin": "foreground", "summary": "keep", "created_at": 1767225600, "target_status": {"state": "ready"}},
        {"id": "four", "action": "add", "target": "memory", "origin": "background_review", "summary": "keep", "created_at": 1767225600, "target_status": {"state": "ready"}},
        {"id": "five", "action": "add", "target": "memory", "origin": "foreground", "summary": "keep", "created_at": 1767225600, "target_status": {"state": "missing"}},
        {"id": "six", "action": "add", "target": "memory", "origin": "foreground", "summary": "different", "created_at": "invalid", "target_status": {"state": "unknown"}},
    ]
    original = json.dumps(records)
    filters = {"query": "keep", "action": "add", "target": "memory", "origin": "foreground", "state": "ready", "fromDate": "2026-01-01", "toDate": "2026-01-01"}
    assert [r["id"] for r in filter_records(records, filters)] == ["one"]
    assert [r["id"] for r in filter_records(records, {})] == [r["id"] for r in records]
    assert json.dumps(records) == original


def test_pending_filters_unknown_dates_and_inclusive_boundaries():
    records = [
        {"id": "start", "action": "batch", "created_at": 1767225600, "target_status": {"state": "unknown"}},
        {"id": "end", "action": "batch", "created_at": 1767311999, "target_status": {"state": "unknown"}},
        {"id": "bad", "action": "batch", "created_at": "nonsense", "target_status": {"state": "unknown"}},
        {"id": "absent", "action": "batch", "target_status": {}},
    ]
    assert [r["id"] for r in filter_records(records, {"state": "unknown"})] == ["start", "end", "bad", "absent"]
    assert [r["id"] for r in filter_records(records, {"fromDate": "2026-01-01", "toDate": "2026-01-01"})] == ["start", "end"]
    assert [r["id"] for r in filter_records(records, {"fromDate": "2026-01-03"})] == []


def test_pending_filters_controls_and_whole_queue_scope():
    pending = SOURCE.split("function PendingWritesPage", 1)[1].split("function StoredMemoryPage", 1)[0]
    assert "Clear filters" in pending
    assert "Pending action filter" in pending
    assert "Pending target filter" in pending
    assert "Pending origin filter" in pending
    assert "Pending status filter" in pending
    assert "Pending date from" in pending and "Pending date to" in pending
    assert "filtered.length" in pending and "records.length" in pending
    assert "all ${records.length} pending write(s)" in pending
    assert "decide(bulkConfirm, 'all')" in pending
    assert "whole queue" in pending.lower()
