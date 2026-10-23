"""MAGI-005 snapshot-bound review diff regressions."""
import json
import subprocess
from pathlib import Path

from test_desktop import FakeCompactionLlm, FakeMemoryStore, client_for, load_api

ROOT = Path(__file__).resolve().parents[1]


def test_magi_005_preview_bound_to_original_snapshot(monkeypatch):
    api = load_api()
    original = ["é😀\noriginal line", "second fact, with detail"]
    store = FakeMemoryStore(memory=original)
    llm = FakeCompactionLlm(["é😀; second"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    monkeypatch.setattr(api, "_plugin_llm", lambda: llm)
    preview = client_for(api).post("/memory/memory/compact/preview").json()
    assert preview["success"]
    assert preview["source_entries"] == original
    assert preview["source_fingerprint"] == api._source_fingerprint(preview["source_entries"])
    assert llm.calls[0]["input"][0]["text"] == "\n§\n".join(preview["source_entries"])


def test_magi_005_diff_keeps_identical_lines_and_marks_changes():
    source = (ROOT / "desktop/plugin.js").read_text()
    assert "function buildCompactionDiff(" in source
    script = """
const fs=require('fs'),vm=require('vm'),src=fs.readFileSync('desktop/plugin.js','utf8');
const start=src.indexOf('function buildCompactionDiff('),end=src.indexOf('const CONTROL_TRANSITION',start);
if(start<0||end<0)throw Error('diff helper unavailable');
const before=['same\\nold😀','persist'],after=['same\\nnew😀','persist'];
const diff=vm.runInNewContext(src.slice(start,end)+'\\nbuildCompactionDiff('+JSON.stringify(before)+','+JSON.stringify(after)+')');
console.log(JSON.stringify(diff));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    assert any(row["kind"] == "unchanged" and row["text"] == "same" for row in data)
    assert any(row["kind"] == "removed" and row["text"] == "old😀" for row in data)
    assert any(row["kind"] == "added" and row["text"] == "new😀" for row in data)
    assert any(row["kind"] == "unchanged" and row["text"] == "persist" for row in data)
    assert not any(row["kind"] != "unchanged" and row["text"] == "persist" for row in data)


def test_magi_005_preview_mode_only_reads_and_allows_selection():
    ui = (ROOT / "desktop/plugin.js").read_text()
    assert "Compaction diff" in ui
    assert "Removed" in ui and "Unchanged" in ui and "Added" in ui
    assert "data-selectable-text" in ui
    assert "source_entries" in ui
    assert "setPreviewView('diff')" in ui
    assert "setPreviewView('entries')" in ui
