"""MAGI-006 explicit edit preview and guarded apply."""

import json
import subprocess
from pathlib import Path

import pytest

from test_desktop import FakeMemoryStore, client_for, load_api

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("target", ["memory", "user"])
def test_magi_006_delimiter_rejected_before_mutation(monkeypatch, target):
    api = load_api()
    store = FakeMemoryStore(**{target: ["A" * 100]})
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    response = client_for(api).post(f"/memory/{target}/compact", json={"source_fingerprint": api._source_fingerprint(["A" * 100]), "entries": ["a\n§\nb"]})
    assert response.status_code == 400
    assert store.batch_calls == []


def test_magi_006_client_validation_unicode_budget_and_duplicates():
    source = (ROOT / "desktop/plugin.js").read_text()
    assert "function validateCompactionEdits(" in source
    script = """
const fs=require('fs'),vm=require('vm'),src=fs.readFileSync('desktop/plugin.js','utf8');
const start=src.indexOf('function validateCompactionEdits('),end=src.indexOf('const CONTROL_TRANSITION',start);
if(start<0||end<0)throw Error('edit validator missing');
const f=vm.runInNewContext(src.slice(start,end)+'\\nvalidateCompactionEdits');
const before=['A'.repeat(80),'B'.repeat(80),'C'.repeat(80),'D'.repeat(80),'E'.repeat(80),'F'.repeat(80),'G'.repeat(80),'H'.repeat(80)];
const results=[f(before,['😀 '+ 'x'.repeat(40)]),f(before,['same','same']),f(before,['a\\n§\\nb']),f(before,['']),f(before,['x'.repeat(450)])];
console.log(JSON.stringify(results));
"""
    output = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    valid, duplicate, delimiter, empty, long = json.loads(output.stdout)
    assert valid["valid"] and valid["afterChars"] == 42
    assert all(not invalid["valid"] for invalid in (duplicate, delimiter, empty, long))


def test_magi_006_edit_split_merge_remove_and_confirmation_present():
    source = (ROOT / "desktop/plugin.js").read_text()
    stored = source.split("function StoredMemoryPage", 1)[1].split("function MagiPage", 1)[0]
    for label in ("Edit proposal", "Done editing", "Split", "Merge next", "Remove entry", "Add entry", "Confirm edited compaction"):
        assert label in stored
    assert "proposalEntries" in stored
    assert "confirmEdited" in stored
    assert "proposalValidation.valid" in stored
    assert "setProposalEntries" in stored
    assert "proposed_entries" in stored
