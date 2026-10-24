from test_desktop import FakeMemoryStore, client_for, load_api


def _client_with_store(monkeypatch, store):
    api = load_api()
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    return client_for(api), api


def test_import_preview_returns_add_only_diff_without_mutation(monkeypatch):
    store = FakeMemoryStore(memory=["existing entry"])
    client, _ = _client_with_store(monkeypatch, store)
    response = client.post("/memory/import/preview", json={
        "format_version": 1, "target": "memory", "entries": ["existing entry", "new entry"],
    })
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["additions"] == ["new entry"]
    assert result["existing_count"] == 1
    assert store.memory_entries == ["existing entry"]
    assert store.batch_calls == []


def test_import_preview_and_confirm_preserve_unicode_and_multiline(monkeypatch):
    store = FakeMemoryStore(memory=["existing entry"])
    client, _ = _client_with_store(monkeypatch, store)
    payload = {"format_version": 1, "target": "memory", "entries": ["existing entry", "Café\nline two"]}
    response = client.post("/memory/import/preview", json=payload)
    assert response.status_code == 200
    preview = response.json()
    assert preview["additions"] == ["Café\nline two"]
    assert preview["skipped_count"] == 1
    assert store.memory_entries == ["existing entry"]
    assert store.batch_calls == []
    applied = client.post("/memory/import", json={
        **payload, "source_fingerprints": preview["source_fingerprints"], "confirmed": True,
    })
    assert applied.status_code == 200
    assert applied.json()["outcomes"]["memory"]["status"] == "applied"
    assert store.memory_entries == ["existing entry", "Café\nline two"]


def test_import_requires_confirmation_and_rejects_invalid_documents_before_write(monkeypatch):
    store = FakeMemoryStore(memory=["unchanged"])
    client, api = _client_with_store(monkeypatch, store)
    valid = {"format_version": 1, "target": "memory", "entries": ["new"]}
    response = client.post("/memory/import/preview", json=valid)
    assert response.status_code == 200
    preview = response.json()
    no_confirmation = client.post("/memory/import", json={
        **valid, "source_fingerprints": preview["source_fingerprints"],
    })
    assert no_confirmation.status_code == 400
    cases = [
        {**valid, "format_version": 2}, {**valid, "target": "other"},
        {**valid, "entries": ["dup", "dup"]},
        {**valid, "entries": ["entry" + api._entry_delimiter() + "injected"]},
    ]
    for payload in cases:
        assert client.post("/memory/import/preview", json=payload).status_code == 400
    assert store.memory_entries == ["unchanged"]
    assert store.batch_calls == []


def test_import_rejects_payload_size_and_character_budget(monkeypatch):
    store = FakeMemoryStore(memory=["existing"], memory_char_limit=12)
    client, _ = _client_with_store(monkeypatch, store)
    too_large = client.post("/memory/import/preview", json={
        "format_version": 1, "target": "memory", "entries": ["x" * 1_000_001],
    })
    assert too_large.status_code == 413
    over_budget = client.post("/memory/import/preview", json={
        "format_version": 1, "target": "memory", "entries": ["budget overflow"],
    })
    assert over_budget.status_code == 400
    assert store.memory_entries == ["existing"]
    assert store.batch_calls == []


def test_import_checks_all_targets_before_mutating_either(monkeypatch):
    store = FakeMemoryStore(memory=["current"], user=["profile"], user_char_limit=8)
    client, _ = _client_with_store(monkeypatch, store)
    payload = {"format_version": 1, "target": "both", "entries": {
        "memory": ["new memory"], "user": ["too large for user"],
    }}
    response = client.post("/memory/import/preview", json=payload)
    assert response.status_code == 400
    assert store.memory_entries == ["current"]
    assert store.user_entries == ["profile"]
    assert store.batch_calls == []


def test_import_rejects_stale_preview_and_reports_partial_multi_target_result(monkeypatch):
    class PartialStore(FakeMemoryStore):
        def apply_batch(self, target, operations):
            if target == "user":
                self.batch_calls.append((target, operations))
                return {"success": False, "error": "native batch unavailable"}
            return super().apply_batch(target, operations)

    store = PartialStore(memory=["memory old"], user=["user old"])
    client, _ = _client_with_store(monkeypatch, store)
    payload = {"format_version": 1, "target": "both", "entries": {
        "memory": ["memory new"], "user": ["user new"],
    }}
    response = client.post("/memory/import/preview", json=payload)
    assert response.status_code == 200
    preview = response.json()
    stale = dict(preview["source_fingerprints"], memory="stale")
    rejected = client.post("/memory/import", json={
        **payload, "source_fingerprints": stale, "confirmed": True,
    })
    assert rejected.status_code == 409
    assert store.batch_calls == []
    applied = client.post("/memory/import", json={
        **payload, "source_fingerprints": preview["source_fingerprints"], "confirmed": True,
    })
    assert applied.status_code == 200
    assert applied.json()["partial"] is True
    assert applied.json()["outcomes"]["memory"]["status"] == "applied"
    assert applied.json()["outcomes"]["user"]["status"] == "failed"
    assert store.memory_entries == ["memory old", "memory new"]
    assert store.user_entries == ["user old"]
