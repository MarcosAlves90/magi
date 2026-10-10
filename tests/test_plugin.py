
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT


def load_plugin():
    name = "magi_plugin_test"
    sys.modules.pop(name, None)
    sys.modules.pop(name + ".core", None)
    sys.modules.pop(name + ".runtime_bridge", None)
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    profile_name = "default"

    def __init__(self, settings=None, raise_config=False, llm=None):
        self.settings = settings or {}
        self.raise_config = raise_config
        self.commands = {}
        self.cli_commands = {}
        self.llm = llm
        self.unload_callbacks = []

    def get_config(self, key, default=None):
        if self.raise_config:
            raise RuntimeError("config unavailable")
        return self.settings.get(key, default)

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = {"handler": handler, **kwargs}

    def register_cli_command(self, name, **kwargs):
        self.cli_commands[name] = kwargs

    def on_unload(self, callback):
        self.unload_callbacks.append(callback)


def test_registers_supported_commands(tmp_path):
    plugin = load_plugin()
    ctx = FakeCtx({"home_override": str(tmp_path), "default_page_size": 3, "max_page_size": 7})
    plugin.register(ctx)

    assert set(ctx.commands) == {"magi", "memory-show", "memory-compact-preview"}
    assert set(ctx.cli_commands) == {"magi"}
    assert ctx.commands["magi"]["argument_mode"] == "text"
    assert "<id" in ctx.commands["memory-show"]["args_hint"]
    assert "memory|user" in ctx.commands["memory-compact-preview"]["args_hint"]
    bridge = sys.modules[plugin.__name__ + ".runtime_bridge"]
    assert "_magi_runtime_bridge" not in sys.modules
    assert bridge.get_plugin_llm() is ctx.llm
    assert len(ctx.unload_callbacks) == 1
    ctx.unload_callbacks[0]()
    try:
        bridge.get_plugin_llm()
    except RuntimeError as exc:
        assert "Agent context is unavailable" in str(exc)
    else:
        raise AssertionError("unload must clear the bound plugin context")


class FakeLlm:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.entries if isinstance(self.entries, dict) else {
            "status": "compacted",
            "reason": "",
            "entries": self.entries,
        }
        return SimpleNamespace(
            parsed=payload,
            text=json.dumps(payload),
            provider="default-provider",
            model="default-model",
        )


class FakeSequenceLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]
        payload = response if isinstance(response, dict) else {
            "status": "compacted",
            "reason": "",
            "entries": response,
        }
        return SimpleNamespace(
            parsed=payload,
            text=json.dumps(payload),
            provider="default-provider",
            model="default-model",
        )


class FakeSchemaFailureLlm(FakeSequenceLlm):
    def complete_structured(self, **kwargs):
        if not self.calls:
            self.calls.append(kwargs)
            raise ValueError("Plugin LLM structured output did not match schema: entries must be an array")
        return super().complete_structured(**kwargs)


class FakePreviewStore:
    def __init__(self, memory=None, user=None):
        self.memory_entries = list(memory or [])
        self.user_entries = list(user or [])


def test_compaction_preview_uses_default_llm_and_returns_reviewable_proposal(monkeypatch, tmp_path):
    plugin = load_plugin()
    llm = FakeLlm(["fact one; fact two detail"])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(
        memory=[
            "The first durable fact is fact one and this sentence contains unnecessary explanation.",
            "The second durable fact is fact two with detail and this sentence repeats unnecessary context.",
        ],
        user=["profile fact"],
    )
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["target"] == "memory"
    assert result["proposed_entries"] == ["fact one; fact two detail"]
    assert result["source_fingerprint"] == plugin._source_fingerprint(store.memory_entries)
    assert result["before_tokens"] > result["after_tokens"]
    assert result["token_estimate_method"] == "hermes_memory_budget_2.75_chars_per_token"
    assert result["provider"] == "default-provider"
    assert result["model"] == "default-model"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert "provider" not in call
    assert "model" not in call
    assert "untrusted data" in call["system_prompt"].lower()
    assert "fewest coherent" in call["system_prompt"].lower()
    assert "do not" in call["instructions"].lower()


def test_compaction_preview_uses_configured_model(monkeypatch, tmp_path):
    plugin = load_plugin()
    llm = FakeLlm(["fact one; fact two detail"])
    ctx = FakeCtx(
        {"home_override": str(tmp_path), "compaction_model": "provider/compact-model"},
        llm=llm,
    )
    store = FakePreviewStore(
        memory=[
            "The first durable fact is fact one and this sentence contains unnecessary explanation.",
            "The second durable fact is fact two with detail and this sentence repeats unnecessary context.",
        ]
    )
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert llm.calls[0]["model"] == "provider/compact-model"
    assert "provider" not in llm.calls[0]


def test_compaction_preview_retries_when_first_proposal_does_not_reduce_footprint(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["The user prefers concise answers.", "The user values precise technical detail."]
    llm = FakeSequenceLlm(
        [
            original,
            ["Concise, precise answers."],
        ]
    )
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["attempts"] == 2
    assert len(llm.calls) == 2
    assert "validity requirement" in llm.calls[0]["instructions"].lower()
    assert "hard output budget" in llm.calls[0]["instructions"].lower()
    assert "previous proposal" in llm.calls[1]["instructions"].lower()
    assert "compress this candidate in place" in llm.calls[1]["instructions"].lower()
    assert llm.calls[1]["input"][0]["text"] == plugin._entry_delimiter().join(original)
    assert result["after_tokens"] < result["before_tokens"]


def test_compaction_preview_rejects_same_length_single_character_proposal(monkeypatch, tmp_path):
    plugin = load_plugin()
    llm = FakeLlm(["Y"])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=["X"])
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is False
    assert result["attempts"] == 3
    assert store.memory_entries == ["X"]


def test_compaction_preview_retries_when_proposal_breaks_advertised_character_budget(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]
    llm = FakeSequenceLlm([["X" * 270], ["Y" * 120]])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["attempts"] == 2
    assert len(llm.calls) == 2
    assert "above its" in llm.calls[1]["instructions"].lower()
    assert llm.calls[1]["input"][0]["text"] == "X" * 270
    assert result["after_chars"] == 120


def test_compaction_preview_targets_core_memory_and_fewer_entries(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = [
        "User prefers concise answers and dislikes filler explanations.",
        "User prefers direct answers with minimal repetition.",
        "User is building Magi.",
        "Magi is a plugin for reviewing stored memory.",
        "The plugin should keep memory actions explicit and reviewable.",
        "The plugin must not mutate stored memory before explicit apply.",
        "User values technical precision in implementation discussions.",
        "User wants implementation details to remain technically precise.",
    ]
    compact = [
        "User prefers concise, direct, technically precise answers without filler or repetition.",
        "Magi: keep memory changes explicit/reviewable; never mutate stored memory before Apply.",
    ]
    llm = FakeLlm(compact)
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["before_entry_count"] == 8
    assert result["after_entry_count"] == 2
    assert result["reduction_percent"] >= 40
    call = llm.calls[0]
    assert "maxItems" not in call["json_schema"]["properties"]["entries"]
    instructions = call["instructions"].lower()
    system_prompt = call["system_prompt"].lower()
    assert "source entry boundaries have no semantic value" in system_prompt
    assert "smallest durable representation" in system_prompt
    assert "examples" in system_prompt
    assert "compact fragments" in system_prompt
    assert "repeated subject wrappers" in system_prompt
    assert "at most 2 entries" in instructions


def test_compaction_preview_retries_when_model_ignores_entry_limit(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = [
        f"Durable source fact {index} with a large amount of redundant explanatory wording that should be compressed."
        for index in range(1, 9)
    ]
    llm = FakeSequenceLlm(
        [
            ["a", "b", "c", "d", "e", "f", "g", "h"],
            ["Core facts 1-4.", "Core facts 5-8."],
        ]
    )
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["after_entry_count"] == 2
    assert result["proposed_entries"] == ["Core facts 1-4.", "Core facts 5-8."]
    assert len(llm.calls) == 2
    retry_prompt = llm.calls[1]["instructions"].lower()
    assert "8 entries" in retry_prompt
    assert "maximum of 2" in retry_prompt
    assert llm.calls[1]["input"][0]["text"] == plugin._entry_delimiter().join(original)


def test_compaction_preview_keeps_hard_budget_constant_across_retries(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]
    # The combined source is 409 chars. 215 chars is a 47.4% reduction.
    llm = FakeSequenceLlm([["X" * 280], ["Y" * 215]])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["outcome"] == "proposal"
    assert result["attempts"] == 2
    assert result["after_chars"] == 215
    assert result["reduction_percent"] == 47.4
    assert store.memory_entries == original


def test_compaction_preview_retries_hermes_schema_validation_failure(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]
    llm = FakeSchemaFailureLlm([None, ["Condensed durable memory"]])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["attempts"] == 2
    assert len(llm.calls) == 2
    assert "schema violation" in llm.calls[1]["instructions"]
    assert llm.calls[1]["input"][0]["text"] == plugin._entry_delimiter().join(original)
    assert store.memory_entries == original


def test_compaction_preview_fails_closed_on_three_invalid_responses(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]
    llm = FakeSequenceLlm([{"status": "compacted"}] * 3)
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is False
    assert "after 3 attempts" in result["error"]
    assert len(llm.calls) == 3
    assert store.memory_entries == original


def test_compaction_preview_fails_closed_after_three_hermes_schema_errors(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]

    class AlwaysSchemaFailureLlm:
        def __init__(self):
            self.calls = []

        def complete_structured(self, **kwargs):
            self.calls.append(kwargs)
            raise ValueError("Plugin LLM structured output did not match schema: invalid status")

    llm = AlwaysSchemaFailureLlm()
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is False
    assert "after 3 attempts" in result["error"]
    assert len(llm.calls) == 3
    assert all(call["input"][0]["text"] == plugin._entry_delimiter().join(original) for call in llm.calls)
    assert store.memory_entries == original


def test_compaction_preview_retries_malformed_json_without_losing_source(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]

    class MalformedThenValidLlm:
        def __init__(self):
            self.calls = []

        def complete_structured(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(parsed=None, text="{invalid json")
            return SimpleNamespace(
                parsed={"status": "compacted", "reason": "", "entries": ["Condensed durable memory"]},
                provider="test-provider",
                model="test-model",
            )

    llm = MalformedThenValidLlm()
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["attempts"] == 2
    assert "invalid json" in llm.calls[1]["instructions"].lower()
    assert llm.calls[1]["input"][0]["text"] == plugin._entry_delimiter().join(original)
    assert store.memory_entries == original


def test_compaction_preview_fails_closed_when_model_keeps_ignoring_entry_limit(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = [
        f"Durable source fact {index} with redundant explanatory wording that should be compressed."
        for index in range(1, 9)
    ]
    too_many = ["a", "b", "c", "d", "e", "f", "g", "h"]
    llm = FakeSequenceLlm([too_many, too_many, too_many])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is False
    assert result["after_entry_count"] == 8
    assert "maximum of 2 entries" in result["error"]
    assert len(llm.calls) == 3


def test_compaction_preview_challenges_early_cannot_compact_claim(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]
    llm = FakeSequenceLlm(
        [
            {
                "status": "cannot_compact_further",
                "reason": "I would have to drop a durable fact.",
                "entries": ["X" * 250],
            },
            ["Y" * 170],
        ]
    )
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["outcome"] == "proposal"
    assert result["attempts"] == 2
    assert result["after_chars"] == 170
    assert len(llm.calls) == 2
    retry = llm.calls[1]["instructions"].lower()
    assert "claimed that further compaction was unsafe" in retry
    assert "challenge that claim" in retry


def test_compaction_preview_reports_no_change_when_final_pass_declares_limit(monkeypatch, tmp_path):
    plugin = load_plugin()
    original = ["A" * 100, "B" * 100, "C" * 100, "D" * 100]
    llm = FakeSequenceLlm(
        [
            ["X" * 250],
            ["Y" * 249],
            {
                "status": "cannot_compact_further",
                "reason": "Every remaining clause is a distinct durable constraint; removing one changes future behavior.",
                "entries": ["Z" * 246],
            },
        ]
    )
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    store = FakePreviewStore(memory=original)
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    plugin.register(ctx)

    result = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert result["success"] is True
    assert result["outcome"] == "no_change"
    assert result["attempts"] == 3
    assert result["before_chars"] > result["best_candidate_chars"]
    assert result["required_chars"] < result["best_candidate_chars"]
    assert "could not safely compress" in result["message"].lower()
    assert "memory was not changed" in result["message"].lower()
    assert "distinct durable constraint" in result["reason"].lower()
    assert len(llm.calls) == 3
    final_prompt = llm.calls[2]["instructions"].lower()
    assert "only on this final pass" in final_prompt
    assert "cannot_compact_further" in final_prompt


def test_compaction_preview_rejects_invalid_or_empty_target_without_llm(monkeypatch, tmp_path):
    plugin = load_plugin()
    llm = FakeLlm(["unused"])
    ctx = FakeCtx({"home_override": str(tmp_path)}, llm=llm)
    monkeypatch.setattr(plugin, "_memory_store", lambda: FakePreviewStore())
    plugin.register(ctx)

    invalid = json.loads(ctx.commands["memory-compact-preview"]["handler"]("other"))
    empty = json.loads(ctx.commands["memory-compact-preview"]["handler"]("memory"))

    assert invalid["success"] is False
    assert empty["success"] is False
    assert llm.calls == []


def test_slash_and_shortcut_handlers(tmp_path):
    plugin = load_plugin()
    ctx = FakeCtx({"home_override": str(tmp_path)})
    plugin.register(ctx)

    out = ctx.commands["magi"]["handler"]("list")
    assert "No pending memory writes" in out

    assert "Usage: /memory-show" in ctx.commands["memory-show"]["handler"]("")
    assert "Invalid arguments" in ctx.commands["magi"]["handler"]('"unterminated')
    assert "Invalid arguments" in ctx.commands["memory-show"]["handler"]('"unterminated')


def test_review_falls_back_when_config_unavailable(monkeypatch, tmp_path):
    plugin = load_plugin()
    ctx = FakeCtx(raise_config=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    review = plugin._review(ctx)
    assert review.home == tmp_path.resolve()
    assert review.default_page_size == 20
    assert review.max_page_size == 100


def test_setup_cli_and_cli_handler(tmp_path, capsys):
    plugin = load_plugin()
    ctx = FakeCtx({"home_override": str(tmp_path)})
    plugin.register(ctx)

    import argparse
    parser = argparse.ArgumentParser()
    setup = ctx.cli_commands["magi"]["setup_fn"]
    setup(parser)
    ns = parser.parse_args(["stats"])
    handler = ctx.cli_commands["magi"]["handler_fn"]
    handler(ns)
    out = capsys.readouterr().out
    assert "Valid records: 0" in out
