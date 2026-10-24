import json

from test_desktop import FakeMemoryStore, client_for, load_api


class FakeOrganizationLlm:
    def __init__(self, payload=None, error=None, text=None, unparsed=False):
        self.payload = payload
        self.error = error
        self.text = text if text is not None else json.dumps(payload)
        self.unparsed = unparsed
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return type("Result", (), {
            "parsed": None if self.unparsed else self.payload,
            "text": self.text,
            "provider": "test-provider",
            "model": "test-model",
        })()


def _client(monkeypatch, store, llm):
    api = load_api()
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    monkeypatch.setattr(api, "_plugin_llm", lambda: llm)
    return client_for(api), api


def test_explicit_organization_analysis_returns_source_verified_suggestions_without_mutation(monkeypatch):
    memory = ["Prefers tea in the morning.", "Likes tea every morning.", "Avoids tea in the morning.", "x" * 600]
    user = ["Works remotely on Tuesdays."]
    store = FakeMemoryStore(memory=list(memory), user=list(user))
    llm = FakeOrganizationLlm({"suggestions": [
        {
            "kind": "duplicate",
            "entry_indexes": [1, 2],
            "reason": "These entries may express the same preference.",
            "evidence": [
                {"entry_index": 1, "quote": "Prefers tea in the morning."},
                {"entry_index": 2, "quote": "Likes tea every morning."},
            ],
            "uncertainty": "The wording differs, so confirm the intended nuance.",
        },
        {
            "kind": "grouping",
            "entry_indexes": [1, 2],
            "reason": "Both entries concern morning tea preferences.",
            "evidence": [
                {"entry_index": 1, "quote": "Prefers tea"},
                {"entry_index": 2, "quote": "Likes tea"},
            ],
            "uncertainty": "The entries may describe different circumstances.",
        },
        {
            "kind": "possible_contradiction",
            "entry_indexes": [1, 3],
            "reason": "The entries express different morning tea preferences.",
            "evidence": [
                {"entry_index": 1, "quote": "Prefers tea in the morning."},
                {"entry_index": 3, "quote": "Avoids tea in the morning."},
            ],
            "uncertainty": "Dates or settings may explain the difference.",
        },
        {
            "kind": "long_entry",
            "entry_indexes": [4],
            "reason": "This source is unusually long.",
            "evidence": [{"entry_index": 4, "quote": "x" * 250}],
            "uncertainty": "Its full detail may still be important.",
        },
        {
            "kind": "long_entry",
            "entry_indexes": [1],
            "reason": "This source is unusually long.",
            "evidence": [{"entry_index": 1, "quote": "Prefers tea"}],
            "uncertainty": "This might be incorrect.",
        },
        {
            "kind": [],
            "entry_indexes": [1, 2],
            "reason": "Invalid kind type.",
            "evidence": [],
            "uncertainty": "Invalid kind.",
        },
        {
            "kind": "unknown",
            "entry_indexes": [1, 2],
            "reason": "Unsupported kind.",
            "evidence": [],
            "uncertainty": "Unsupported.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": "1,2",
            "reason": "Invalid references.",
            "evidence": [],
            "uncertainty": "Invalid.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": [1],
            "reason": "Insufficient references.",
            "evidence": [],
            "uncertainty": "Insufficient.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": [1, 1],
            "reason": "Duplicate references.",
            "evidence": [],
            "uncertainty": "Duplicate.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": [1, 99],
            "reason": "Potentially conflicting references.",
            "evidence": [{"entry_index": 1, "quote": "Prefers tea"}],
            "uncertainty": "The second source may not exist.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": [1, 2],
            "reason": "",
            "evidence": [],
            "uncertainty": "Missing reason.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": [1, 99],
            "reason": "Valid reason.",
            "evidence": [{"entry_index": 1, "quote": "Prefers tea"}],
            "uncertainty": "Valid uncertainty.",
        },
        {
            "kind": "duplicate",
            "entry_indexes": [1, 2],
            "reason": "Valid reason.",
            "evidence": [{"entry_index": 1, "quote": "fabricated evidence"}],
            "uncertainty": "Valid uncertainty.",
        },
    ]})
    client, _ = _client(monkeypatch, store, llm)

    assert client.get("/memory").status_code == 200
    assert llm.calls == []
    response = client.post("/memory/memory/organization/analyze")

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["target"] == "memory"
    assert result["analyzed_entry_count"] == len(memory)
    assert [item["kind"] for item in result["suggestions"]] == [
        "duplicate", "grouping", "possible_contradiction", "long_entry"
    ]
    suggestion = result["suggestions"][0]
    assert suggestion["kind"] == "duplicate"
    assert suggestion["entry_indexes"] == [1, 2]
    assert suggestion["evidence"][0]["quote"] == memory[0]
    assert [source["content"] for source in result["sources"][:2]] == memory[:2]
    assert result["discarded_suggestions"] == 10
    assert len(llm.calls) == 1
    assert llm.calls[0]["json_mode"] is True
    assert llm.calls[0]["schema_name"] == "memory_organization_analysis"
    assert llm.calls[0]["json_schema"]["properties"]["suggestions"]["maxItems"] == 20
    assert "untrusted" in llm.calls[0]["system_prompt"].lower()
    assert "model" not in llm.calls[0]
    assert store.memory_entries == memory
    assert store.user_entries == user


def test_organization_analysis_refuses_oversized_memory_without_calling_model(monkeypatch):
    store = FakeMemoryStore(memory=["x" * 20_001])
    llm = FakeOrganizationLlm({"suggestions": []})
    client, _ = _client(monkeypatch, store, llm)

    response = client.post("/memory/memory/organization/analyze")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "limit" in response.json()["error"].lower()
    assert llm.calls == []
    assert store.memory_entries == ["x" * 20_001]


def test_organization_analysis_limits_entry_count_and_skips_empty_memory(monkeypatch):
    oversized_store = FakeMemoryStore(memory=[f"entry {index}" for index in range(101)])
    llm = FakeOrganizationLlm({"suggestions": []})
    client, _ = _client(monkeypatch, oversized_store, llm)
    oversized = client.post("/memory/memory/organization/analyze").json()
    assert oversized["success"] is False
    assert llm.calls == []

    empty_store = FakeMemoryStore(memory=[])
    client, _ = _client(monkeypatch, empty_store, llm)
    empty = client.post("/memory/memory/organization/analyze").json()
    assert empty["success"] is False
    assert "empty" in empty["error"].lower()
    assert llm.calls == []


def test_organization_analysis_redacts_provider_errors_and_does_not_mutate(monkeypatch):
    memory = ["A durable fact."]
    store = FakeMemoryStore(memory=list(memory), user=["Another fact."])
    llm = FakeOrganizationLlm(error=RuntimeError("provider-token-secret"))
    client, _ = _client(monkeypatch, store, llm)

    response = client.post("/memory/memory/organization/analyze")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "provider-token-secret" not in response.text
    assert "not changed" in response.json()["error"].lower()
    assert len(llm.calls) == 1
    assert store.memory_entries == memory
    assert store.user_entries == ["Another fact."]


def test_organization_analysis_accepts_json_fallback_and_rejects_malformed_output(monkeypatch):
    store = FakeMemoryStore(memory=["A durable fact."])
    llm = FakeOrganizationLlm({"suggestions": []}, unparsed=True)
    client, _ = _client(monkeypatch, store, llm)
    result = client.post("/memory/memory/organization/analyze").json()
    assert result["success"] is True
    assert result["suggestions"] == []
    assert len(llm.calls) == 1

    llm = FakeOrganizationLlm(text="not valid JSON", unparsed=True)
    client, _ = _client(monkeypatch, store, llm)
    invalid = client.post("/memory/memory/organization/analyze").json()
    assert invalid["success"] is False
    assert "invalid structured output" in invalid["error"]
    assert len(llm.calls) == 1
    assert store.memory_entries == ["A durable fact."]
