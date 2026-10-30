"""Profile-local IPC regression and hardening tests."""

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeLlm:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise RuntimeError(self.error)
        return SimpleNamespace(
            parsed={"suggestions": []},
            text='{"suggestions": []}',
            provider="test-provider",
            model="test-model",
        )


def _load_runtime_ipc():
    path = ROOT / "runtime_ipc.py"
    if not path.is_file():
        pytest.skip("profile IPC implementation is not present on the baseline")
    spec = importlib.util.spec_from_file_location("magi_runtime_ipc_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reject_request(module, payload):
    server_side, client_side = socket.socketpair()
    errors = []

    def send_request():
        try:
            client_side.sendall(json.dumps(payload).encode("utf-8"))
            client_side.shutdown(socket.SHUT_WR)
        except OSError as exc:
            errors.append(exc)

    sender = threading.Thread(target=send_request)
    sender.start()
    try:
        with pytest.raises(ValueError):
            module._read_request(server_side)
    finally:
        sender.join(timeout=2)
        server_side.close()
        client_side.close()
    assert not sender.is_alive()
    assert not errors


def test_dashboard_llm_uses_profile_socket_across_processes_and_unload_removes_it(tmp_path, monkeypatch):
    profile = (tmp_path / "active-profile").resolve()
    profile.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile))

    spec = importlib.util.spec_from_file_location(
        "hermes_plugin_magi_ipc_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin
    spec.loader.exec_module(plugin)
    monkeypatch.setattr(plugin, "resolve_hermes_home", lambda override="": profile)
    llm = FakeLlm()
    unload_callbacks = []
    ctx = SimpleNamespace(
        llm=llm,
        on_unload=unload_callbacks.append,
        register_command=lambda *args, **kwargs: None,
        register_cli_command=lambda *args, **kwargs: None,
    )
    plugin.register(ctx)

    child = r'''
import importlib.util, os
from pathlib import Path
root = Path(os.environ["MAGI_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location("magi_dashboard_child", root / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
api.resolve_hermes_home = lambda *args, **kwargs: Path(os.environ["HERMES_HOME"])
result = api._plugin_llm().complete_structured(
    instructions="Analyze this synthetic test entry.",
    input=[{"type": "text", "text": "synthetic test entry"}],
    json_schema={"type": "object"},
    purpose="memory organization analysis",
)
assert result.parsed == {"suggestions": []}
assert result.text == '{"suggestions": []}'
assert result.provider == "test-provider"
assert result.model == "test-model"
print("IPC_RESULT_OK")
'''
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "SystemRoot", "COMSPEC", "PATHEXT", "USERPROFILE", "LOCALAPPDATA")
        if key in os.environ
    }
    env.update({"HERMES_HOME": str(profile), "MAGI_PLUGIN_ROOT": str(ROOT)})
    try:
        result = subprocess.run(
            [sys.executable, "-c", child], cwd=ROOT, env=env, text=True,
            capture_output=True, timeout=15, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "IPC_RESULT_OK" in result.stdout
        assert len(llm.calls) == 1
        assert "synthetic test entry" in str(llm.calls[0]["input"])
        socket_path = profile / "plugin-data" / "magi" / "llm.sock"
        assert socket_path.is_socket()
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
    finally:
        for callback in unload_callbacks:
            callback()
        for name in tuple(sys.modules):
            if name == spec.name or name.startswith(spec.name + "."):
                sys.modules.pop(name, None)
    assert not socket_path.exists()


def test_duplicate_server_stop_preserves_live_owner(tmp_path):
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir()
    llm = FakeLlm()
    owner = module.ProfileLlmServer(profile, llm).start()
    duplicate = module.ProfileLlmServer(profile, llm).start()
    client = module.ProfileLlmClient(profile)
    try:
        assert client.is_available
        duplicate.stop()
        assert client.is_available
        result = client.complete_structured(
            instructions="test", input=[{"type": "text", "text": "synthetic"}],
            json_schema={"type": "object"}, purpose="test",
        )
        assert result.parsed == {"suggestions": []}
        assert len(llm.calls) == 1
    finally:
        duplicate.stop()
        owner.stop()
    assert not client.is_available


def test_concurrent_server_starts_serialize_profile_endpoint(tmp_path):
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir()
    llm = FakeLlm()
    barrier = threading.Barrier(3)
    guard = threading.Lock()
    servers = []
    errors = []

    def start_server():
        try:
            barrier.wait(timeout=2)
            server = module.ProfileLlmServer(profile, llm).start()
            with guard:
                servers.append(server)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=start_server, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert len(servers) == 2

    client = module.ProfileLlmClient(profile)
    owners = [server for server in servers if server._server is not None]
    duplicates = [server for server in servers if server._server is None]
    try:
        assert len(owners) == 1
        assert len(duplicates) == 1
        assert client.is_available
        duplicates[0].stop()
        assert client.is_available
    finally:
        for server in servers:
            server.stop()
    assert not client.is_available


def test_unload_waits_for_inflight_structured_request(tmp_path):
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir()
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    errors = []

    class BlockingLlm:
        def complete_structured(self, **kwargs):
            entered.set()
            if not release.wait(timeout=3):
                raise TimeoutError("synthetic test request timed out")
            return SimpleNamespace(parsed={"ok": True}, text="{}", provider="test", model="fake")

    server = module.ProfileLlmServer(profile, BlockingLlm()).start()
    client = module.ProfileLlmClient(profile)

    def request():
        try:
            client.complete_structured(
                instructions="test", input=[{"type": "text", "text": "synthetic"}],
                json_schema={"type": "object"}, purpose="test",
            )
        except Exception as exc:
            errors.append(exc)

    request_thread = threading.Thread(target=request, daemon=True)
    stop_thread = threading.Thread(target=lambda: (server.stop(), stopped.set()), daemon=True)
    request_thread.start()
    assert entered.wait(timeout=1)
    stop_thread.start()
    try:
        assert not stopped.wait(timeout=0.05)
    finally:
        release.set()
    assert stopped.wait(timeout=2)
    request_thread.join(timeout=2)
    stop_thread.join(timeout=2)
    assert not request_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not errors
    assert not client.is_available


def test_long_home_rejects_untrusted_tmpdir_and_recovers_stale_socket(tmp_path):
    module = _load_runtime_ipc()
    profile = tmp_path / ("profile-" + "x" * 96)
    profile.mkdir()
    assert module.profile_socket_path(profile).name == module._LONG_HOME_SOCKET_NAME

    socket_path = module.profile_socket_path(profile)
    runtime_root = socket_path.parents[1]
    module._ensure_private_runtime_root(runtime_root)
    socket_path.parent.symlink_to(profile, target_is_directory=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()

    llm = FakeLlm()
    server = module.ProfileLlmServer(profile, llm).start()
    client = module.ProfileLlmClient(profile)
    try:
        assert client.is_available
        assert stat.S_IMODE(runtime_root.stat().st_mode) == 0o700
        assert socket_path.is_socket()
        assert (profile / "plugin-data" / "magi" / "llm.sock").is_symlink()

        unsafe = tmp_path / "world-readable-tmp"
        unsafe.mkdir(mode=0o755)
        os.chmod(unsafe, 0o755)
        with pytest.raises(RuntimeError, match="not trusted"):
            module._ensure_private_runtime_root(unsafe / "magi-ipc")

        unsafe_parent = tmp_path / "world-writable-parent"
        unsafe_parent.mkdir(mode=0o777)
        os.chmod(unsafe_parent, 0o777)
        private_child = unsafe_parent / "private-temp"
        private_child.mkdir(mode=0o700)
        with pytest.raises(RuntimeError, match="not trusted"):
            module._ensure_private_runtime_root(private_child / "magi-ipc")
    finally:
        server.stop()
    assert not client.is_available
    assert not (profile / module._LONG_HOME_SOCKET_NAME).exists()
    assert not (profile / "plugin-data" / "magi" / "llm.sock").exists()


def test_structured_request_validation_bounds_nested_schema_and_rejects_unknown_method():
    module = _load_runtime_ipc()
    valid = {
        "version": 1,
        "method": "complete_structured",
        "kwargs": {
            "instructions": "test",
            "input": [{"type": "text", "text": "synthetic"}],
            "json_schema": {"type": "object"},
            "purpose": "test",
        },
    }
    bad_method = json.loads(json.dumps(valid))
    bad_method["method"] = "complete"
    _reject_request(module, bad_method)

    bad_item = json.loads(json.dumps(valid))
    bad_item["kwargs"]["input"][0]["unexpected"] = "x"
    _reject_request(module, bad_item)

    deep_schema = json.loads(json.dumps(valid))
    nested = "leaf"
    for _ in range(module._MAX_SCHEMA_DEPTH + 1):
        nested = {"nested": nested}
    deep_schema["kwargs"]["json_schema"] = nested
    _reject_request(module, deep_schema)

    oversized_schema = json.loads(json.dumps(valid))
    oversized_schema["kwargs"]["json_schema"] = {"type": "object", "description": "x" * 300_000}
    _reject_request(module, oversized_schema)


def test_invalid_response_shape_and_provider_exception_are_redacted(tmp_path):
    module = _load_runtime_ipc()
    with pytest.raises(ValueError):
        module._result_payload(SimpleNamespace(parsed=["not-an-object"], text="", provider="", model=""))

    profile = tmp_path / "profile"
    profile.mkdir()
    server = module.ProfileLlmServer(profile, FakeLlm(error="SECRET_PROVIDER_DETAIL")).start()
    client = module.ProfileLlmClient(profile)
    try:
        with pytest.raises(RuntimeError) as error:
            client.complete_structured(
                instructions="test", input=[{"type": "text", "text": "synthetic"}],
                json_schema={"type": "object"}, purpose="test",
            )
        assert "SECRET_PROVIDER_DETAIL" not in str(error.value)
    finally:
        server.stop()


def test_long_home_start_creates_fresh_profile_alias(tmp_path, monkeypatch):
    module = _load_runtime_ipc()
    runtime_tmp = tmp_path / "magi-ipc-test"
    runtime_tmp.mkdir(mode=0o700)
    profile = runtime_tmp / ("profile-" + "x" * 100)
    profile.mkdir(mode=0o700)
    socket_path = module.profile_socket_path(profile)
    assert not socket_path.parent.exists()
    server = None
    try:
        server = module.ProfileLlmServer(profile, FakeLlm()).start()
        assert server._alias_path.is_symlink()
        assert socket_path.is_socket()
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    finally:
        if server is not None:
            server.stop()
        shutil.rmtree(runtime_tmp, ignore_errors=True)


def test_long_home_rejects_user_owned_symlink_tmpdir(tmp_path, monkeypatch):
    module = _load_runtime_ipc()
    runtime_tmp = tmp_path / "magi-ipc-symlink-test"
    runtime_tmp.mkdir(mode=0o700)
    target = runtime_tmp / "private-tmp"
    target.mkdir(mode=0o700)
    os.chmod(target, 0o700)
    link = runtime_tmp / "tmp-link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("TMPDIR", str(link))
    profile = runtime_tmp / ("profile-" + "x" * 100)
    profile.mkdir(mode=0o700)
    server = module.ProfileLlmServer(profile, FakeLlm())
    started_server = None
    try:
        with pytest.raises(RuntimeError, match="symlink|trusted"):
            started_server = server.start()
    finally:
        if started_server is not None:
            started_server.stop()
        shutil.rmtree(runtime_tmp, ignore_errors=True)


def test_profile_lock_rejects_hard_linked_lock_file(tmp_path):
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    outside = tmp_path / "outside-lock-target"
    outside.write_text("synthetic test lock target", encoding="utf-8")
    os.chmod(outside, 0o600)
    lock_path = profile / ".magi-llm-ipc.lock"
    os.link(outside, lock_path)
    server = module.ProfileLlmServer(profile, FakeLlm())
    try:
        with pytest.raises(RuntimeError, match="lock file"):
            server.start()
    finally:
        lock_path.unlink(missing_ok=True)
        if server._server is not None:
            server.stop()
    assert not (profile / "plugin-data").exists()


@pytest.mark.parametrize("listener_protocol", [1, 999])
def test_start_rejects_incompatible_listener_without_removing_it(tmp_path, listener_protocol):
    module = _load_runtime_ipc()
    runtime_tmp = tmp_path / "magi-ipc-listener-test"
    runtime_tmp.mkdir(mode=0o700)
    os.chmod(runtime_tmp, 0o700)
    profile = (runtime_tmp / "profile").resolve()
    profile.mkdir(mode=0o700)
    public_path = profile / module._SOCKET_RELATIVE_PATH
    socket_path = module.profile_socket_path(profile)
    if socket_path == public_path:
        socket_path.parent.mkdir(parents=True, mode=0o700)
        os.chmod(socket_path.parent, 0o700)
    else:
        runtime_root = socket_path.parents[1]
        runtime_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(runtime_root, 0o700)
        socket_path.parent.symlink_to(profile, target_is_directory=True)
        public_path.parent.mkdir(parents=True, mode=0o700)
        public_path.symlink_to(profile / module._LONG_HOME_SOCKET_NAME)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    listener.settimeout(2)
    listener.listen(1)
    identity = (socket_path.lstat().st_dev, socket_path.lstat().st_ino)
    probe_finished = threading.Event()

    def respond_to_probe():
        try:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(1)
                try:
                    if connection.recv(4096):
                        connection.sendall(json.dumps({"ok": True, "protocol": listener_protocol}).encode())
                except OSError:
                    pass
        finally:
            probe_finished.set()

    responder = threading.Thread(target=respond_to_probe, daemon=True)
    responder.start()
    server = module.ProfileLlmServer(profile, FakeLlm())
    try:
        with pytest.raises(RuntimeError, match="incompatible"):
            server.start()
        assert socket_path.is_socket()
    finally:
        listener.close()
        responder.join(timeout=2)
        if probe_finished.is_set() and socket_path.exists():
            current = socket_path.lstat()
            if (current.st_dev, current.st_ino) == identity:
                socket_path.unlink()
        if public_path.is_symlink():
            public_path.unlink()
        if socket_path.parent.is_symlink():
            socket_path.parent.unlink()
        shutil.rmtree(runtime_tmp, ignore_errors=True)
    assert not responder.is_alive()


def test_unload_waits_past_client_timeout_for_provider_return(tmp_path):
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir()
    entered = threading.Event()
    release = threading.Event()
    request_finished = threading.Event()
    stopped = threading.Event()
    errors = []

    class BlockingLlm:
        def complete_structured(self, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("synthetic test request timed out")
            return SimpleNamespace(parsed={"ok": True}, text="{}", provider="test", model="fake")

    server = module.ProfileLlmServer(profile, BlockingLlm()).start()
    client = module.ProfileLlmClient(profile, timeout=0.05)

    def request():
        try:
            client.complete_structured(
                instructions="test", input=[{"type": "text", "text": "synthetic"}],
                json_schema={"type": "object"}, purpose="test",
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            request_finished.set()

    request_thread = threading.Thread(target=request, daemon=True)
    stop_thread = threading.Thread(target=lambda: (server.stop(), stopped.set()), daemon=True)
    request_thread.start()
    assert entered.wait(timeout=1)
    try:
        assert request_finished.wait(timeout=1)
        assert errors
        stop_thread.start()
        assert not stopped.wait(timeout=0.1)
    finally:
        release.set()
    assert stopped.wait(timeout=2)
    request_thread.join(timeout=2)
    stop_thread.join(timeout=2)
    assert not request_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not client.is_available


def test_server_readiness_signal_does_not_depend_on_polling(tmp_path, monkeypatch):
    module = _load_runtime_ipc()
    monkeypatch.delenv("TMPDIR", raising=False)
    socket_path = tmp_path.parent / "r"
    server = module._ThreadedUnixServer(str(socket_path), FakeLlm())
    server.service_actions = lambda: None
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
    )
    thread.start()
    try:
        assert server.ready.wait(timeout=0.2)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        socket_path.unlink(missing_ok=True)
    assert not thread.is_alive()


def test_failed_start_cleanup_drains_inflight_provider_call(tmp_path, monkeypatch):
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir()
    entered = threading.Event()
    release = threading.Event()
    wait_entered = threading.Event()
    allow_start_failure = threading.Event()
    request_finished = threading.Event()
    startup_finished = threading.Event()
    request_errors = []
    startup_errors = []

    class BlockingLlm:
        def complete_structured(self, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("synthetic test request timed out")
            return SimpleNamespace(parsed={"ok": True}, text="{}", provider="test", model="fake")

    original_server = module._ThreadedUnixServer

    class ControlledReadinessServer(original_server):
        def __init__(self, path, llm):
            super().__init__(path, llm)

            def wait_for_failure(timeout=None):
                wait_entered.set()
                allow_start_failure.wait(timeout=3)
                return False

            self.ready.wait = wait_for_failure

    monkeypatch.setattr(module, "_ThreadedUnixServer", ControlledReadinessServer)
    server = module.ProfileLlmServer(profile, BlockingLlm())
    client = module.ProfileLlmClient(profile, timeout=0.05)

    def start_server():
        try:
            server.start()
        except Exception as exc:
            startup_errors.append(exc)
        finally:
            startup_finished.set()

    def request():
        try:
            client.complete_structured(
                instructions="test", input=[{"type": "text", "text": "synthetic"}],
                json_schema={"type": "object"}, purpose="test",
            )
        except Exception as exc:
            request_errors.append(exc)
        finally:
            request_finished.set()

    start_thread = threading.Thread(target=start_server, daemon=True)
    request_thread = threading.Thread(target=request, daemon=True)
    start_thread.start()
    try:
        assert wait_entered.wait(timeout=1)
        request_thread.start()
        assert entered.wait(timeout=1)
        assert request_finished.wait(timeout=1)
        assert request_errors
        allow_start_failure.set()
        assert not startup_finished.wait(timeout=0.1)
    finally:
        release.set()
        allow_start_failure.set()
    assert startup_finished.wait(timeout=2)
    start_thread.join(timeout=2)
    request_thread.join(timeout=2)
    assert not start_thread.is_alive()
    assert not request_thread.is_alive()
    assert startup_errors and "did not become ready" in str(startup_errors[0])
    assert not server.path.exists()
    assert not client.is_available


def test_profile_lock_file_open_is_nonblocking(tmp_path, monkeypatch):
    if not hasattr(os, "O_NONBLOCK") or not hasattr(socket, "AF_UNIX"):
        pytest.skip("nonblocking Unix lock flags are unavailable")
    module = _load_runtime_ipc()
    profile = tmp_path / "profile"
    profile.mkdir()
    original_open = os.open
    observed_flags = []

    def check_lock_open(path, flags, *args, **kwargs):
        if path == ".magi-llm-ipc.lock":
            observed_flags.append(flags)
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", check_lock_open)
    server = module.ProfileLlmServer(profile, FakeLlm())
    started = False
    try:
        server.start()
        started = True
    finally:
        if started:
            server.stop()
    assert observed_flags
