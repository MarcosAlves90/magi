import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class FakeLlm:
    def __init__(self):
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        parsed = {"status": "compacted", "reason": "", "entries": ["Condensed fact"]}
        return SimpleNamespace(parsed=parsed, text=json.dumps(parsed), provider="default", model="default")


class FakeContext:
    def __init__(self, llm):
        self.llm = llm
        self.settings = {
            "compaction_routing": {"provider": "removed", "model": "removed-model"},
            "compaction_provider": "openai",
            "compaction_model": "gpt-4.1-mini",
        }
        self.commands = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler

    def register_cli_command(self, *args, **kwargs):
        pass

    def has_capability(self, capability):
        return True


def _load_plugin():
    name = "magi_llm_routing_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_agent_preview_uses_defaults_when_saved_routing_disappears(monkeypatch):
    plugin = _load_plugin()
    llm = FakeLlm()
    ctx = FakeContext(llm)
    store = SimpleNamespace(
        memory_entries=["The first durable fact has unnecessary explanation.", "The second fact has extra wording."],
        user_entries=[],
    )
    monkeypatch.setattr(plugin, "_memory_store", lambda: store)
    monkeypatch.setattr(plugin, "_available_llm_options", lambda: [{"id": "openai", "models": ["gpt-4.1-mini"]}], raising=False)

    try:
        plugin.register(ctx)
        result = json.loads(ctx.commands["memory-compact-preview"]("memory"))
    finally:
        sys.modules[plugin.__name__ + ".runtime_bridge"].clear_plugin_context(ctx)

    assert result["success"] is True
    assert "provider" not in llm.calls[0]
    assert "model" not in llm.calls[0]
