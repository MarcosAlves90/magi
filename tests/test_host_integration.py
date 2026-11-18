"""Exercise the native Hermes plugin host with Magi Agent and dashboard together."""

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_native_plugin_host_compaction_bridge_reload_and_unload(tmp_path):
    hermes_root = Path.home() / ".hermes" / "hermes-agent"
    if not (hermes_root / "hermes_cli" / "plugin_host_child.py").is_file():
        pytest.skip("Native Hermes checkout is not installed")

    code = r'''
import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.environ["HERMES_NATIVE_ROOT"])
import hermes_bootstrap  # noqa: F401
from hermes_cli.plugin_host_child import HostRuntime
from hermes_cli.plugin_host_wire import decode
from tools.memory_tool import load_on_disk_store

store = load_on_disk_store()
assert store.add("memory", "First durable detail, with repeated descriptive context.")["success"]
assert store.add("memory", "Second durable detail, with extra redundant explanation.")["success"]

runtime = object.__new__(HostRuntime)
runtime.modules = {}
runtime.loads = {}
runtime.profiles = {}
runtime.instance_modules = {}
runtime.unload_callbacks = {}
runtime.asgi_apps = {}
runtime.refs = {}
runtime.owners = {}
runtime._lock = threading.Lock()
runtime.loop = asyncio.new_event_loop()
thread = threading.Thread(target=runtime.loop.run_forever, daemon=True)
thread.start()
registrations = []
llm_calls = []

def ctx_call(plugin, method, *args, **kwargs):
    registrations.append((plugin, method))
    return None

def facade_call(plugin, facade, method, *args, _probe=False, **kwargs):
    assert (plugin, facade, method) == ("magi", "llm", "complete_structured")
    if _probe:
        return {"__method__": True, "async": False}
    llm_calls.append(kwargs)
    return SimpleNamespace(
        parsed={"status": "compacted", "reason": "", "entries": ["First and second durable details."]},
        text="", provider="stub-provider", model="stub-model",
    )

runtime.ctx_call = ctx_call
runtime.facade_call = facade_call
plugin_dir = Path(os.environ["MAGI_REPO_ROOT"])
load_params = {
    "plugin_key": "magi", "plugin_id": "magi", "name": "magi",
    "path": str(plugin_dir), "module_name": "hermes_plugins.magi",
    "manifest": {"name": "magi"},
    "ctx_methods": ["register_command", "register_cli_command", "get_config", "set_config"],
    "profile_name": "default",
}
request = {
    "dashboard_dir": str(plugin_dir / "dashboard"), "api_file": "plugin_api.py",
    "plugin": "magi", "method": "POST",
    "path": "/memory/memory/compact/preview", "query": "", "body": b"", "headers": [],
}

def preview():
    response = decode(runtime.op_asgi(request), None)
    assert response["status"] == 200, response
    return json.loads(response["body"])

try:
    runtime.op_load(load_params)
    assert ("magi", "register_command") in registrations
    assert preview()["success"] is True
    assert len(llm_calls) == 1
    request.update(method="GET", path="/llm/options", body=b"")
    options = decode(runtime.op_asgi(request), None)
    assert options["status"] == 200, options
    request.update(method="PUT", path="/llm/settings", body=b'{"provider":"","model":""}', headers=[["content-type", "application/json"]])
    saved = decode(runtime.op_asgi(request), None)
    assert saved["status"] == 200, saved
    assert ("magi", "set_config") in registrations
    request.update(method="POST", path="/memory/memory/compact/preview", body=b"")
    assert runtime.op_unload({"plugin_key": "magi"})["errors"] == []
    unavailable = preview()
    assert unavailable["success"] is False
    assert "Agent context is unavailable" in unavailable["error"]
    runtime.op_load(load_params)
    assert preview()["success"] is True
    assert len(llm_calls) == 2
    assert runtime.op_unload({"plugin_key": "magi"})["errors"] == []
finally:
    runtime.loop.call_soon_threadsafe(runtime.loop.stop)
    thread.join(timeout=2)
'''
    env = {**os.environ, "HERMES_HOME": str(tmp_path),
           "HERMES_NATIVE_ROOT": str(hermes_root), "MAGI_REPO_ROOT": str(ROOT)}
    result = subprocess.run(
        ["uv", "run", "--with", "fastapi>=0.115,<1", "--with", "httpx>=0.27,<1",
         "python", "-c", code],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
