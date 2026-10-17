import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
DESKTOP = ROOT / "desktop"


def stage(home: Path, pid: str, payload: dict, summary="summary", origin="foreground", created=1):
    pending = home / "pending" / "memory"
    pending.mkdir(parents=True, exist_ok=True)
    record = {
        "id": pid,
        "subsystem": "memory",
        "action": payload.get("action", ""),
        "summary": summary,
        "origin": origin,
        "created_at": created,
        "payload": payload,
    }
    (pending / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")


def load_api():
    manifest_path = DASHBOARD / "manifest.json"
    assert manifest_path.is_file(), "desktop plugin manifest is required"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {"name": "magi", "api": "plugin_api.py"}

    api_path = DASHBOARD / manifest["api"]
    assert api_path.is_file()
    name = "magi_dashboard_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, api_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def client_for(api):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_backend_agent_context_bridge_load_unload_reload_without_global_alias():
    """The real Agent registration must be discoverable by the separate API module."""
    api = load_api()
    package = "hermes_plugin_magi_bridge_integration"

    def load_agent():
        spec = importlib.util.spec_from_file_location(
            package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
        )
        plugin = importlib.util.module_from_spec(spec)
        sys.modules[package] = plugin
        spec.loader.exec_module(plugin)
        return plugin

    def register_agent(plugin):
        callbacks = []
        llm = object()
        ctx = SimpleNamespace(
            llm=llm,
            on_unload=callbacks.append,
            register_command=lambda *args, **kwargs: None,
            register_cli_command=lambda *args, **kwargs: None,
        )
        plugin.register(ctx)
        return llm, callbacks

    def unload():
        for name in tuple(sys.modules):
            if name == package or name.startswith(package + "."):
                sys.modules.pop(name)

    try:
        plugin = load_agent()
        llm, callbacks = register_agent(plugin)
        assert api._plugin_llm() is llm
        assert "_magi_runtime_bridge" not in sys.modules
        callbacks[0]()
        with pytest.raises(RuntimeError, match="context is unavailable"):
            api._plugin_llm()

        unload()
        plugin = load_agent()
        replacement, callbacks = register_agent(plugin)
        assert api._plugin_llm() is replacement
        callbacks[0]()
        unload()
        with pytest.raises(RuntimeError, match="context is unavailable"):
            api._plugin_llm()
    finally:
        unload()


def test_unified_desktop_package_uses_documented_surfaces():
    load_api()
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    assert "@hermes/plugin-sdk" in source
    assert "ROUTES_AREA" in source
    assert "SIDEBAR_NAV_AREA" in source
    assert "id: 'magi'" in source
    assert "name: 'Magi'" in source
    assert "data: { path: '/magi'" in source
    assert "label: 'Magi'" in source
    assert "codicon: 'wand'" in source
    assert "ctx.rest('/records')" in source
    assert "ctx.rest('/memory')" in source
    assert "method: 'PUT'" in source
    assert "refetchInterval" in source
    assert "querySelector" not in source
    assert "document." not in source


def test_desktop_decisions_use_session_independent_backend():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    decision_start = source.index("const runDecision")
    decision_end = source.index("ctx.registerMany", decision_start)
    decision_source = source[decision_start:decision_end]
    assert "ctx.rest" in decision_source
    assert "/decision" in decision_source
    assert "slash.exec" not in decision_source
    assert "activeSessionId" not in decision_source
    assert "focusedSessionId" not in decision_source
    assert "['overview', 'Overview']" in source
    assert "useState('overview')" in source
    assert "Approve all" in source and "Reject all" in source
    assert "Confirm ${bulkConfirm} all" in source
    assert "target missing · obsolete proposal" in source
    assert "Delete obsolete" in source
    assert "Cannot approve" in source


def test_pending_detail_text_is_selectable_for_copying():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")

    overview_start = source.index("function OverviewView")
    overview_end = source.index("function PendingWritesPage", overview_start)
    overview_source = source[overview_start:overview_end]
    assert "'data-selectable-text': 'true'" in overview_source

    detail_start = source.index("? view === 'overview'")
    detail_end = source.index(": jsx('div', {", detail_start)
    detail_source = source[detail_start:detail_end]
    assert "'data-selectable-text': 'true'" in detail_source


def test_entry_viewers_are_vertically_resizable():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    pending_start = source.index("function PendingWritesPage")
    pending_end = source.index("function StoredMemoryPage", pending_start)
    pending_source = source[pending_start:pending_end]
    stored_start = source.index("function StoredMemoryPage")
    stored_end = source.index("function MagiPage", stored_start)
    stored_source = source[stored_start:stored_end]

    assert "RESIZABLE_ENTRY_VIEWER" in source
    assert "resize-y" in source
    assert "data-resizable-entry-viewer" in pending_source
    assert pending_source.count("data-resizable-entry-viewer") >= 1
    assert stored_source.count("data-resizable-entry-viewer") >= 2
    assert "resize-none" not in stored_source


def test_stored_memory_layout_groups_metrics_and_toolbar():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    stored = source.split("function StoredMemoryPage", 1)[1].split("function MagiPage", 1)[0]

    # The summary has one row of values followed by a full-width budget row.
    assert "sm:grid-cols-3" in stored
    assert stored.count("jsx(CompactStat,") == 3
    first_row = stored.index("label: 'Entries'")
    second_row = stored.index("label: 'Estimated tokens'")
    third_row = stored.index("label: 'Characters'")
    budget = stored.index("children: 'Character budget'")
    assert first_row < second_row < third_row < budget
    assert "border-t border-(--ui-stroke-secondary) pt-2" in stored
    assert "label: 'Entries'" in stored and "detail: targetLabel" in stored
    assert "label: 'Characters'" in stored and "targetData.char_limit" in stored
    assert "label: 'Estimated tokens'" in stored and "targetData.estimated_token_limit" in stored
    assert "jsx(UsageBar, { percent: targetData.usage_percent })" in stored
    assert "targetData.usage_percent >= 80" in stored
    assert "Math.max(0, (targetData.char_limit ?? 0) - (targetData.used_chars ?? 0))" in stored
    # One search and one action set, directly before the entry list/split pane.
    assert stored.count("jsx(SearchBox,") == 1
    assert stored.index("jsx(SearchBox,") < stored.index("jsxs('aside',")
    assert stored.index("STORED_TARGETS.map") < stored.index("jsxs('aside',")
    assert stored.index("onClick: beginAdd") < stored.index("jsxs('aside',")
    assert stored.index("onClick: compact") < stored.index("jsxs('aside',")


def test_stored_memory_toolbar_is_responsive_and_accessible():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    stored = source.split("function StoredMemoryPage", 1)[1].split("function MagiPage", 1)[0]

    assert "flex flex-wrap items-center gap-2" in stored
    assert "min-w-48 flex-1" in stored
    assert "'aria-label': 'Stored memory target'" in stored
    assert "'aria-pressed': target === id" in stored
    assert "focus-visible:outline" in stored
    assert "ariaLabel: 'Search stored memory'" in stored
    assert "'aria-live': 'polite'" in stored
    assert "disabled: compacting || applyingCompaction || saving || deleting" in stored
    assert "compacting || applyingCompaction || saving || deleting || !targetData.count" in stored


def test_pending_review_filter_only_selects_visible_proposals():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    pending = source.split("function PendingWritesPage", 1)[1].split("function StoredMemoryPage", 1)[0]

    assert "const selectedVisible = filtered.some(record => record.id === selected)" in pending
    assert "const currentId = selectedVisible ? selected : filtered[0]?.id || ''" in pending
    assert "records[0]?.id" not in pending
    assert "filtered.length ? jsxs('main'" in pending


def test_pending_review_empty_queue_is_single_clear_state_with_compact_summary():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    pending = source.split("function PendingWritesPage", 1)[1].split("function StoredMemoryPage", 1)[0]

    assert "'aria-label': 'Pending queue summary'" in pending
    assert "jsx(StatCard" not in pending
    assert "records.length === 0 && !listing.isLoading" in pending
    assert "title: 'Nothing to review'" in pending
    assert "'Stored memory'" in pending


def test_replacement_operations_show_responsive_labeled_before_and_after():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    operation = source.split("function OperationCard", 1)[1].split("function OverviewView", 1)[0]
    content = source.split("function ContentBlock", 1)[1].split("function OperationCard", 1)[0]

    assert "xl:grid-cols-2" in operation
    assert "label: 'Before · current entry'" in operation
    assert "label: 'After · proposed replacement'" in operation
    assert "Original entry unavailable in this proposal" in operation
    assert "side: 'before'" in operation and "side: 'after'" in operation
    assert "'data-change-side': side" in content


def test_compaction_preview_explicitly_compares_current_and_proposed_statistics():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    stored = source.split("function StoredMemoryPage", 1)[1].split("function MagiPage", 1)[0]

    assert "label: 'Current · before'" in stored
    assert "label: 'Proposed · after'" in stored
    assert "label: 'Estimated reduction'" in stored
    assert "children: 'Proposed memory entries'" in stored
    assert "'Apply compaction'" in stored
    assert "'Cancel preview'" in stored


def test_backend_decisions_do_not_require_chat_session(monkeypatch, tmp_path):
    stage(
        tmp_path,
        "approve123",
        {"action": "replace", "target": "memory", "old_text": "old", "matched_entry": "old", "content": "new"},
    )
    stage(
        tmp_path,
        "reject123",
        {"action": "add", "target": "memory", "content": "discard me"},
        created=2,
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api = load_api()
    store = FakeMemoryStore(memory=["old"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    monkeypatch.setattr(
        api,
        "_apply_pending_memory",
        lambda payload, active_store: active_store.replace(
            payload["target"],
            payload["old_text"],
            payload["content"],
            matched_entry=payload["matched_entry"],
        ),
    )
    client = client_for(api)

    approved = client.post("/records/approve123/decision", json={"action": "approve"})
    rejected = client.post("/records/reject123/decision", json={"action": "reject"})

    assert approved.status_code == 200
    assert approved.json()["success"] is True
    assert "Approved 1 memory write(s)." in approved.json()["output"]
    assert store.memory_entries == ["new"]
    assert not (tmp_path / "pending" / "memory" / "approve123.json").exists()

    assert rejected.status_code == 200
    assert rejected.json()["success"] is True
    assert "Rejected pending memory write 'reject123'." in rejected.json()["output"]
    assert not (tmp_path / "pending" / "memory" / "reject123.json").exists()


def test_backend_pending_records_follow_request_scoped_hermes_home(
    monkeypatch, tmp_path, hermes_home_context
):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    stage(
        home_a,
        "approve123",
        {"action": "replace", "target": "memory", "old_text": "a-old", "matched_entry": "a-old", "content": "a-new"},
        summary="profile A approval",
    )
    stage(
        home_a,
        "reject123",
        {"action": "add", "target": "memory", "content": "keep A pending"},
        summary="profile A rejection",
        created=2,
    )
    stage(
        home_b,
        "approve123",
        {"action": "replace", "target": "memory", "old_text": "b-old", "matched_entry": "b-old", "content": "b-new"},
        summary="profile B approval",
    )
    stage(
        home_b,
        "reject123",
        {"action": "add", "target": "memory", "content": "discard B pending"},
        summary="profile B rejection",
        created=2,
    )

    monkeypatch.setenv("HERMES_HOME", str(home_a))
    api = load_api()
    stores = {
        home_a.resolve(): FakeMemoryStore(memory=["a-old"]),
        home_b.resolve(): FakeMemoryStore(memory=["b-old"]),
    }
    monkeypatch.setattr(
        api,
        "_memory_store",
        lambda: stores[Path(hermes_home_context.get_hermes_home()).resolve()],
    )
    monkeypatch.setattr(
        api,
        "_apply_pending_memory",
        lambda payload, active_store: active_store.replace(
            payload["target"],
            payload["old_text"],
            payload["content"],
            matched_entry=payload["matched_entry"],
        ),
    )
    client = client_for(api)

    token = set_hermes_home_override(home_b)
    try:
        listing = client.get("/records")
        approved = client.post("/records/approve123/decision", json={"action": "approve"})
        rejected = client.post("/records/reject123/decision", json={"action": "reject"})
    finally:
        reset_hermes_home_override(token)

    assert listing.status_code == 200
    assert [record["summary"] for record in listing.json()["records"]] == [
        "profile B rejection",
        "profile B approval",
    ]
    assert approved.status_code == 200
    assert rejected.status_code == 200
    assert stores[home_b.resolve()].memory_entries == ["b-new"]
    assert stores[home_a.resolve()].memory_entries == ["a-old"]
    assert not list((home_b / "pending" / "memory").glob("*.json"))
    assert sorted(path.name for path in (home_a / "pending" / "memory").glob("*.json")) == [
        "approve123.json",
        "reject123.json",
    ]


def test_backend_bulk_decisions_do_not_require_chat_session(monkeypatch, tmp_path):
    stage(tmp_path, "add-one", {"action": "add", "target": "memory", "content": "one"})
    stage(tmp_path, "add-two", {"action": "add", "target": "memory", "content": "two"}, created=2)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api = load_api()
    store = FakeMemoryStore()
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    monkeypatch.setattr(
        api,
        "_apply_pending_memory",
        lambda payload, active_store: active_store.add(payload["target"], payload["content"]),
    )
    client = client_for(api)

    approved = client.post("/records/all/decision", json={"action": "approve"})

    assert approved.status_code == 200
    assert "Approved 2 memory write(s)." in approved.json()["output"]
    assert store.memory_entries == ["one", "two"]

    stage(tmp_path, "drop-one", {"action": "add", "target": "memory", "content": "three"}, created=3)
    stage(tmp_path, "drop-two", {"action": "add", "target": "memory", "content": "four"}, created=4)
    rejected = client.post("/records/all/decision", json={"action": "reject"})

    assert rejected.status_code == 200
    assert "Rejected 2 pending memory write(s)." in rejected.json()["output"]
    assert not list((tmp_path / "pending" / "memory").glob("*.json"))


def test_backend_lists_and_renders_pending_records(monkeypatch, tmp_path):
    stage(
        tmp_path,
        "abc123",
        {"action": "replace", "target": "memory", "old_text": "old", "matched_entry": "old", "content": "new"},
        summary="replace preference",
        origin="background_review",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api = load_api()
    monkeypatch.setattr(api, "_memory_store", lambda: FakeMemoryStore(memory=["old"]))
    client = client_for(api)

    listing = client.get("/records")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["count"] == 1
    assert payload["issues"] == []
    assert payload["records"][0] == {
        "id": "abc123",
        "action": "replace",
        "target": "memory",
        "origin": "background_review",
        "summary": "replace preference",
        "created_at": 1.0,
        "target_status": {
            "state": "ready",
            "can_apply": True,
            "missing_count": 0,
            "destructive_count": 1,
        },
    }

    detail = client.get("/records/abc123")
    assert detail.status_code == 200
    data = detail.json()
    assert data["id"] == "abc123"
    assert data["payload"] == {
        "action": "replace",
        "target": "memory",
        "old_text": "old",
        "matched_entry": "old",
        "content": "new",
    }
    assert "Pending memory write abc123" in data["proposal"]
    assert "old" in data["diff"] and "new" in data["diff"]
    assert '"id": "abc123"' in data["raw"]
    assert "OK abc123" in data["verify"]


class FakeMemoryStore:
    def __init__(
        self,
        memory=None,
        user=None,
        replace_result=None,
        add_result=None,
        remove_result=None,
        batch_result=None,
        memory_char_limit=2200,
        user_char_limit=1375,
    ):
        self.memory_entries = list(memory or [])
        self.user_entries = list(user or [])
        self.replace_result = replace_result
        self.add_result = add_result
        self.remove_result = remove_result
        self.batch_result = batch_result
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self.calls = []
        self.add_calls = []
        self.remove_calls = []
        self.batch_calls = []

    def _char_count(self, target):
        entries = self.memory_entries if target == "memory" else self.user_entries
        return len("\n§\n".join(entries))

    def _char_limit(self, target):
        return self.memory_char_limit if target == "memory" else self.user_char_limit

    def replace(self, target, old_text, content, matched_entry=None):
        self.calls.append((target, old_text, content, matched_entry))
        if self.replace_result is not None:
            return self.replace_result
        entries = self.memory_entries if target == "memory" else self.user_entries
        index = entries.index(matched_entry)
        entries[index] = content
        return {"success": True, "message": "Entry replaced.", "replaced_entry": old_text}

    def add(self, target, content):
        self.add_calls.append((target, content))
        if self.add_result is not None:
            return self.add_result
        entries = self.memory_entries if target == "memory" else self.user_entries
        if content not in entries:
            entries.append(content)
        return {"success": True, "message": "Entry added."}

    def remove(self, target, old_text, matched_entry=None):
        self.remove_calls.append((target, old_text, matched_entry))
        if self.remove_result is not None:
            return self.remove_result
        entries = self.memory_entries if target == "memory" else self.user_entries
        entries.remove(matched_entry)
        return {"success": True, "message": "Entry removed.", "previous_content": matched_entry}

    def _mutate(self, target, mutate, *, skip_drift=False):
        entries = self.memory_entries if target == "memory" else self.user_entries
        result = mutate(entries, self._char_limit(target))
        if isinstance(result, dict):
            return result
        if target == "memory":
            self.memory_entries = result[0]
        else:
            self.user_entries = result[0]
        return {"success": True, "message": result[1]}

    def apply_batch(self, target, operations):
        self.batch_calls.append((target, operations))
        if self.batch_result is not None:
            return self.batch_result

        def apply(entries, limit):
            working = list(entries)
            for operation in operations:
                if operation["action"] == "remove":
                    working.remove(operation["matched_entry"])
                elif operation["action"] == "add":
                    if operation["content"] not in working:
                        working.append(operation["content"])
            return working, "Applied compaction."

        return self._mutate(target, apply)


def test_backend_marks_pending_write_obsolete_when_pinned_target_is_missing(monkeypatch, tmp_path):
    stage(
        tmp_path,
        "stale123",
        {"action": "remove", "target": "memory", "old_text": "gone", "matched_entry": "gone"},
        summary="remove stale memory",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api = load_api()
    monkeypatch.setattr(api, "_memory_store", lambda: FakeMemoryStore(memory=["still here"]))
    client = client_for(api)

    listing_status = client.get("/records").json()["records"][0]["target_status"]
    detail_status = client.get("/records/stale123").json()["record"]["target_status"]

    assert listing_status == detail_status
    assert detail_status["state"] == "missing"
    assert detail_status["can_apply"] is False
    assert detail_status["missing_count"] == 1
    assert "reject it to delete the proposal" in detail_status["message"].lower()


def test_backend_marks_batch_obsolete_when_any_pinned_target_is_missing(monkeypatch, tmp_path):
    stage(
        tmp_path,
        "batch-stale",
        {
            "action": "batch",
            "target": "user",
            "operations": [
                {"action": "replace", "old_text": "present", "matched_entry": "present", "content": "new"},
                {"action": "remove", "old_text": "gone", "matched_entry": "gone"},
                {"action": "add", "content": "added"},
            ],
        },
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api = load_api()
    monkeypatch.setattr(api, "_memory_store", lambda: FakeMemoryStore(user=["present"]))

    status = client_for(api).get("/records/batch-stale").json()["record"]["target_status"]

    assert status["state"] == "missing"
    assert status["can_apply"] is False
    assert status["missing_count"] == 1
    assert status["destructive_count"] == 2


class FakeCompactionLlm:
    def __init__(self, entries=None):
        self.entries = list(entries or ["first fact; second fact"])
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed={"entries": self.entries},
            text=json.dumps({"entries": self.entries}),
            provider="default-provider",
            model="default-model",
        )


def test_backend_lists_stored_memory_for_both_targets(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(memory=["agent note", "second note"], user=["user profile"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).get("/memory")
    assert response.status_code == 200
    body = response.json()
    assert "estimated_tokens" in body["targets"]["memory"], "token estimate metadata is required"
    assert body == {
        "targets": {
            "memory": {
                "target": "memory",
                "count": 2,
                "used_chars": 24,
                "char_limit": 2200,
                "usage_percent": 1.1,
                "estimated_tokens": 9,
                "estimated_token_limit": 800,
                "token_estimate_method": "hermes_memory_budget_2.75_chars_per_token",
                "entries": [
                    {"index": 0, "content": "agent note"},
                    {"index": 1, "content": "second note"},
                ],
            },
            "user": {
                "target": "user",
                "count": 1,
                "used_chars": 12,
                "char_limit": 1375,
                "usage_percent": 0.9,
                "estimated_tokens": 4,
                "estimated_token_limit": 500,
                "token_estimate_method": "hermes_memory_budget_2.75_chars_per_token",
                "entries": [{"index": 0, "content": "user profile"}],
            },
        }
    }


def test_backend_replaces_exact_stored_memory_entry(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(memory=["old entry"], user=["profile"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).put(
        "/memory/memory",
        json={"old_text": "old entry", "content": "new entry"},
    )
    assert response.status_code == 200
    assert store.calls == [("memory", "old entry", "new entry", "old entry")]
    body = response.json()
    assert body["success"] is True
    assert body["target"]["entries"] == [{"index": 0, "content": "new entry"}]


def test_backend_adds_stored_memory_entry_through_memory_store(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(memory=["existing"], user=["profile"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).post("/memory/memory/entries", json={"content": "new entry"})

    assert response.status_code == 200
    assert store.add_calls == [("memory", "new entry")]
    assert response.json()["target"]["entries"] == [
        {"index": 0, "content": "existing"},
        {"index": 1, "content": "new entry"},
    ]


def test_backend_deletes_exact_stored_memory_entry_through_memory_store(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(memory=["keep", "remove me"], user=["profile"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).request(
        "DELETE",
        "/memory/memory/entries",
        json={"old_text": "remove me"},
    )

    assert response.status_code == 200
    assert store.remove_calls == [("memory", "remove me", "remove me")]
    assert response.json()["target"]["entries"] == [{"index": 0, "content": "keep"}]


def test_backend_surfaces_add_remove_failures_without_mutation_success(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(
        memory=["existing"],
        add_result={"success": False, "error": "Memory limit exceeded."},
        remove_result={"success": False, "error": "Entry changed since it was reviewed."},
    )
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    add_response = client_for(api).post("/memory/memory/entries", json={"content": "too large"})
    remove_response = client_for(api).request(
        "DELETE",
        "/memory/memory/entries",
        json={"old_text": "existing"},
    )

    assert add_response.status_code == 409
    assert remove_response.status_code == 409
    assert add_response.json()["detail"]["success"] is False
    assert remove_response.json()["detail"]["success"] is False


def test_backend_rejects_invalid_stored_memory_target(monkeypatch):
    api = load_api()
    monkeypatch.setattr(api, "_memory_store", lambda: FakeMemoryStore())
    response = client_for(api).put(
        "/memory/other",
        json={"old_text": "old", "content": "new"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "target must be 'memory' or 'user'."


def test_backend_surfaces_memory_store_failure_without_success(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(
        memory=["old entry"],
        replace_result={"success": False, "error": "Entry changed since it was reviewed."},
    )
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    response = client_for(api).put(
        "/memory/memory",
        json={"old_text": "old entry", "content": "new entry"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["success"] is False
    assert "changed" in response.json()["detail"]["error"]


def test_desktop_stored_memory_mode_exposes_both_targets_and_editing():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    assert "Pending writes" in source
    assert "Stored memory" in source
    assert "['memory', 'Memory']" in source
    assert "['user', 'User']" in source
    assert "Save changes" in source
    assert "Add entry" in source
    assert "Delete entry" in source
    assert "Confirm delete" in source
    assert "Search stored memory" in source
    assert "estimated_tokens" in source
    assert "estimated_token_limit" in source
    assert "usage_percent" in source
    assert "used_chars" in source
    assert "char_limit" in source
    assert "used" in source
    assert "tokens estimated" in source
    assert "Compact with AI" in source
    assert "AI compaction in progress" in source
    assert "compactionElapsed" in source
    assert "setInterval" in source
    assert "automatically retry" in source
    stored_start = source.index("function StoredMemoryPage")
    stored_end = source.index("function MagiPage", stored_start)
    stored_source = source[stored_start:stored_end]
    assert "max-h-[44vh]" in stored_source
    assert "shrink-0" in stored_source
    assert "overflow-hidden" in stored_source
    assert "before_entry_count" in stored_source
    assert "reduction_percent" in stored_source
    assert "preview?.outcome === 'no_change'" in stored_source
    assert "kind: 'info'" in stored_source
    assert "Apply compaction" in source
    assert "Cancel preview" in source
    preview_start = source.index("const requestCompaction")
    preview_end = source.index("const applyCompaction", preview_start)
    preview_source = source[preview_start:preview_end]
    assert "/compact/preview" in preview_source
    assert "ctx.rest" in preview_source
    assert "slash.exec" not in preview_source
    assert "activeSessionId" not in preview_source
    assert "focusedSessionId" not in preview_source
    assert "/entries" in source


def test_desktop_dashboard_uses_lightweight_reactive_ui_patterns():
    source = (DESKTOP / "plugin.js").read_text(encoding="utf-8")
    assert "function StatCard" in source
    assert "function UsageBar" in source
    assert "function EmptyState" in source
    assert "function LoadingRows" in source
    assert "duration-100" in source
    assert "duration-150" in source
    assert "motion-reduce:transition-none" in source
    assert "animate-in fade-in-0" in source
    assert "animate-pulse" in source
    assert "framer-motion" not in source
    assert "gsap" not in source
    assert "lottie" not in source


def test_backend_generates_compaction_preview_without_a_chat_session(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(
        memory=[
            "The first durable fact is first fact with unnecessary explanatory wording.",
            "The second durable fact is second fact with detail and repeated explanatory wording.",
        ],
        user=["profile"],
    )
    llm = FakeCompactionLlm(["first fact; second fact with detail"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    monkeypatch.setattr(api, "_plugin_llm", lambda: llm, raising=False)

    response = client_for(api).post("/memory/memory/compact/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["target"] == "memory"
    assert body["provider"] == "default-provider"
    assert body["model"] == "default-model"
    assert body["proposed_entries"] == ["first fact; second fact with detail"]
    assert body["source_fingerprint"] == api._source_fingerprint(store.memory_entries)
    assert body["before_tokens"] > body["after_tokens"]
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert "provider" not in call
    assert "model" not in call
    assert "untrusted data" in call["system_prompt"].lower()
    assert "fewest coherent" in call["system_prompt"].lower()
    assert "preserve" in call["instructions"].lower()


def test_backend_compaction_preview_uses_configured_model(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(
        memory=[
            "The first durable fact is first fact with unnecessary explanatory wording.",
            "The second durable fact is second fact with detail and repeated explanatory wording.",
        ],
    )
    llm = FakeCompactionLlm(["first fact; second fact with detail"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    monkeypatch.setattr(api, "_plugin_llm", lambda: llm, raising=False)
    monkeypatch.setattr(api, "_settings", lambda: {"compaction_model": "provider/compact-model"})

    response = client_for(api).post("/memory/memory/compact/preview")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert llm.calls[0]["model"] == "provider/compact-model"
    assert "provider" not in llm.calls[0]


def test_backend_applies_compaction_as_one_stale_safe_batch(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(memory=["first fact", "second fact"], user=["profile"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    fingerprint = api._source_fingerprint(store.memory_entries)

    response = client_for(api).post(
        "/memory/memory/compact",
        json={"source_fingerprint": fingerprint, "entries": ["both facts"]},
    )

    assert response.status_code == 200
    assert len(store.batch_calls) == 1
    target, operations = store.batch_calls[0]
    assert target == "memory"
    assert operations == [
        {"action": "remove", "old_text": "first fact", "matched_entry": "first fact"},
        {"action": "remove", "old_text": "second fact", "matched_entry": "second fact"},
        {"action": "add", "content": "both facts"},
    ]
    assert response.json()["target"]["entries"] == [{"index": 0, "content": "both facts"}]


@pytest.mark.parametrize("target", ["memory", "user"])
@pytest.mark.parametrize("proposed", [
    ["A" * 75],  # 25% reduction misses the advertised 40% minimum.
    ["A" * 61],  # One character over the hard ceiling must also fail.
    ["A" * 20, "B" * 20],  # An over-count result must never be applied.
    ["A" * 100],  # An unchanged proposal is not compaction.
])
def test_backend_rejects_compaction_that_does_not_meet_preview_contract(monkeypatch, target, proposed):
    api = load_api()
    store = FakeMemoryStore(**{target: ["A" * 100]})
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).post(
        f"/memory/{target}/compact",
        json={"source_fingerprint": api._source_fingerprint(["A" * 100]), "entries": proposed},
    )

    assert response.status_code == 400
    assert store.batch_calls == []
    assert getattr(store, f"{target}_entries") == ["A" * 100]


@pytest.mark.parametrize("target", ["memory", "user"])
def test_backend_accepts_compaction_at_exact_hard_budget(monkeypatch, target):
    api = load_api()
    store = FakeMemoryStore(**{target: ["A" * 100]})
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).post(
        f"/memory/{target}/compact",
        json={"source_fingerprint": api._source_fingerprint(["A" * 100]), "entries": ["A" * 60]},
    )

    assert response.status_code == 200
    assert getattr(store, f"{target}_entries") == ["A" * 60]


def test_backend_rejects_stale_compaction_without_mutation(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(memory=["newer memory"], user=[])
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).post(
        "/memory/memory/compact",
        json={"source_fingerprint": api._source_fingerprint(["older memory"]), "entries": ["compact"]},
    )

    assert response.status_code == 409
    assert store.batch_calls == []
    assert store.memory_entries == ["newer memory"]


def test_backend_rejects_concurrent_add_during_native_batch_lock(monkeypatch):
    api = load_api()

    class ConcurrentMemoryStore(FakeMemoryStore):
        def _mutate(self, target, mutate, *, skip_drift=False):
            # Hermes reloads the file after taking its exclusive lock. Model a
            # second process that appended memory after Magi's initial read.
            self.memory_entries.append("new fact from concurrent writer")
            return super()._mutate(target, mutate, skip_drift=skip_drift)

    store = ConcurrentMemoryStore(memory=["old first", "old second"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    fingerprint = api._source_fingerprint(list(store.memory_entries))

    response = client_for(api).post(
        "/memory/memory/compact",
        json={"source_fingerprint": fingerprint, "entries": ["old facts"]},
    )

    assert response.status_code == 409
    assert "changed" in str(response.json()).lower()
    assert store.memory_entries == ["old first", "old second", "new fact from concurrent writer"]


def test_compaction_uses_native_hermes_disk_transaction_and_rejects_concurrent_changes(monkeypatch, tmp_path):
    """Test native on-disk Hermes separately from conftest's Hermes stubs."""
    hermes_root = Path.home() / ".hermes" / "hermes-agent"
    if not (hermes_root / "tools" / "memory_tool_store.py").is_file():
        pytest.skip("Native Hermes checkout is not installed")
    code = '''
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["HERMES_NATIVE_ROOT"])
import hermes_bootstrap  # noqa: F401
from tools import memory_tool
from tools.memory_tool_store import MemoryStore
from fastapi import FastAPI
from fastapi.testclient import TestClient

memory_dir = Path(os.environ["HERMES_HOME"]) / "isolated-memory"
memory_tool.get_memory_dir = lambda: memory_dir
writer = MemoryStore()
writer.load_from_disk()
assert writer.add("memory", "old first")["success"]
assert writer.add("memory", "old second")["success"]

api_path = Path(os.environ["MAGI_REPO_ROOT"]) / "dashboard" / "plugin_api.py"
spec = importlib.util.spec_from_file_location("native_hermes_magi_api", api_path)
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
store = MemoryStore()
store.load_from_disk()
api._memory_store = lambda: store
app = FastAPI()
app.include_router(api.router)
client = TestClient(app)
original = api._source_fingerprint(list(store.memory_entries))

# A separate native MemoryStore writes after Magi's first fingerprint check.
native_mutate = store._mutate
def write_before_native_lock(target, mutation, *, skip_drift=False):
    assert writer.add("memory", "concurrent third fact")["success"]
    return native_mutate(target, mutation, skip_drift=skip_drift)
store._mutate = write_before_native_lock
response = client.post("/memory/memory/compact", json={
    "source_fingerprint": original, "entries": ["old facts"]})
assert response.status_code == 409, response.text
disk = MemoryStore()
disk.load_from_disk()
assert disk.memory_entries == ["old first", "old second", "concurrent third fact"]

del store._mutate
store.load_from_disk()
refreshed = api._source_fingerprint(list(store.memory_entries))
response = client.post("/memory/memory/compact", json={
    "source_fingerprint": refreshed, "entries": ["all facts"]})
assert response.status_code == 200, response.text
disk.load_from_disk()
assert disk.memory_entries == ["all facts"]
assert "_mutate" not in vars(store)
'''
    env = {**os.environ, "HERMES_HOME": str(tmp_path),
           "HERMES_NATIVE_ROOT": str(hermes_root), "MAGI_REPO_ROOT": str(ROOT)}
    result = subprocess.run(
        ["uv", "run", "--with", "fastapi>=0.115,<1", "--with", "httpx>=0.27,<1", "python", "-c", code],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_backend_surfaces_compaction_batch_failure_without_partial_write(monkeypatch):
    api = load_api()
    store = FakeMemoryStore(
        memory=["original durable fact with extra detail"],
        batch_result={"success": False, "error": "Compaction rejected."},
    )
    monkeypatch.setattr(api, "_memory_store", lambda: store)

    response = client_for(api).post(
        "/memory/memory/compact",
        json={"source_fingerprint": api._source_fingerprint(store.memory_entries), "entries": ["short fact"]},
    )

    assert response.status_code == 409
    assert len(store.batch_calls) == 1
    assert store.memory_entries == ["original durable fact with extra detail"]


def test_backend_returns_not_found_for_unknown_record(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    api = load_api()
    response = client_for(api).get("/records/missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "No pending memory write with id/prefix 'missing'."
