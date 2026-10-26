"""Profile-local Unix socket transport for explicit Magi structured LLM calls."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import socket
import socketserver
import stat
import struct
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, cast


_PROTOCOL_VERSION = 1
_MAX_REQUEST_BYTES = 1_000_000
_MAX_RESPONSE_BYTES = 2_000_000
_REQUEST_TIMEOUT_SECONDS = 300
_SOCKET_RELATIVE_PATH = Path("plugin-data") / "magi" / "llm.sock"
_LONG_HOME_SOCKET_NAME = ".s"
_MAX_PROMPT_CHARS = 32_000
_MAX_INPUT_CHARS = 250_000
_MAX_SCHEMA_BYTES = 256_000
_MAX_SCHEMA_NODES = 20_000
_MAX_SCHEMA_DEPTH = 24
_ALLOWED_KWARGS = {
    "instructions",
    "input",
    "json_schema",
    "json_mode",
    "schema_name",
    "system_prompt",
    "temperature",
    "purpose",
    "model",
}
_PROFILE_LOCKS_GUARD = threading.Lock()
_PROFILE_LOCKS: dict[str, threading.Lock] = {}


def profile_socket_path(profile_home: Path | str) -> Path:
    """Return a profile-specific socket path, using a short alias for long homes."""
    home = Path(profile_home).expanduser().resolve()
    path = home / _SOCKET_RELATIVE_PATH
    if len(os.fsencode(str(path))) > 90:
        temp_root = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).expanduser()
        runtime_root = temp_root / "magi-ipc"
        profile_key = hashlib.sha256(os.fsencode(str(home))).hexdigest()[:12]
        path = runtime_root / profile_key / _LONG_HOME_SOCKET_NAME
    return path


def _validate_directory_permissions(info: os.stat_result) -> None:
    mode = stat.S_IMODE(info.st_mode)
    uid = os.getuid()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("Magi IPC temporary directory is not trusted.")
    if info.st_uid in {uid, 0} and mode & 0o022 == 0:
        return
    if info.st_uid == 0 and mode & stat.S_ISVTX and mode & 0o002:
        return
    raise RuntimeError("Magi IPC temporary directory is not trusted.")


def _validate_resolved_directory_chain(path: Path) -> None:
    current = path
    while True:
        _validate_directory_permissions(current.lstat())
        parent = current.parent
        if parent == current:
            return
        current = parent


def _validate_source_symlinks(path: Path, seen: frozenset[str] = frozenset()) -> None:
    raw = Path(path).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise RuntimeError("Magi IPC temporary directory is not trusted.")
    current = Path(raw.anchor)
    for component in raw.parts[1:]:
        current /= component
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            key = str(current)
            if info.st_uid != 0 or key in seen:
                raise RuntimeError("Magi IPC temporary directory is not trusted.")
            target = Path(os.readlink(current))
            if not target.is_absolute():
                target = current.parent / target
            _validate_source_symlinks(target, seen | {key})
        elif not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Magi IPC temporary directory is not trusted.")


def _validate_directory_chain(path: Path) -> None:
    raw = Path(path).expanduser()
    _validate_source_symlinks(raw)
    resolved = raw.resolve(strict=True)
    _validate_resolved_directory_chain(resolved)


def _ensure_private_runtime_root(runtime_root: Path) -> None:
    temp_root = runtime_root.parent
    _validate_directory_chain(temp_root)
    info = temp_root.stat()
    mode = stat.S_IMODE(info.st_mode)
    uid = os.getuid()
    safe_temp_root = (info.st_uid == uid and mode & 0o077 == 0) or (
        info.st_uid == 0 and mode & stat.S_ISVTX and mode & 0o002
    )
    if not safe_temp_root:
        raise RuntimeError("Magi IPC temporary directory is not trusted.")
    try:
        runtime_root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = runtime_root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != uid
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("Magi IPC runtime directory is not private.")


@contextmanager
def _profile_endpoint_lock(profile_home: Path) -> Iterator[None]:
    key = str(profile_home)
    with _PROFILE_LOCKS_GUARD:
        process_lock = _PROFILE_LOCKS.setdefault(key, threading.Lock())
    with process_lock:
        try:
            import fcntl
        except ImportError:
            raise RuntimeError("Magi IPC profile locking is unavailable.") from None
        _validate_resolved_directory_chain(profile_home)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(profile_home, directory_flags)
        try:
            directory_info = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.getuid()
                or stat.S_IMODE(directory_info.st_mode) & 0o022
            ):
                raise RuntimeError("Magi IPC profile directory is not trusted.")
            nonblocking_flag = getattr(os, "O_NONBLOCK", None)
            if nonblocking_flag is None:
                raise RuntimeError("Magi IPC requires nonblocking lock-file opens.")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | nonblocking_flag
            descriptor = os.open(".magi-llm-ipc.lock", flags, 0o600, dir_fd=directory_fd)
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_nlink != 1
                ):
                    raise RuntimeError("Magi IPC lock file is not private.")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)


def _receive_all(connection: socket.socket, limit: int) -> bytes:
    chunks = bytearray()
    while True:
        part = connection.recv(min(65536, limit + 1 - len(chunks)))
        if not part:
            return bytes(chunks)
        chunks.extend(part)
        if len(chunks) > limit:
            raise ValueError("IPC message exceeds limit.")


def _encode(value: dict[str, Any], limit: int) -> bytes:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > limit:
        raise ValueError("IPC message exceeds limit.")
    return data


def _validate_json_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > _MAX_SCHEMA_NODES or depth > _MAX_SCHEMA_DEPTH:
        raise ValueError("Structured value exceeds limits.")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > _MAX_INPUT_CHARS:
            raise ValueError("Structured value exceeds limits.")
        return
    if isinstance(value, int):
        if abs(value) > 10**15:
            raise ValueError("Structured value exceeds limits.")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 10**15:
            raise ValueError("Structured value exceeds limits.")
        return
    if isinstance(value, list):
        if len(value) > _MAX_SCHEMA_NODES:
            raise ValueError("Structured value exceeds limits.")
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_SCHEMA_NODES:
            raise ValueError("Structured value exceeds limits.")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 512:
                raise ValueError("Structured value exceeds limits.")
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    raise ValueError("Structured value is not JSON-compatible.")


def _read_request(connection: socket.socket) -> dict[str, Any] | None:
    data = _receive_all(connection, _MAX_REQUEST_BYTES)
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != _PROTOCOL_VERSION:
        raise ValueError("Invalid IPC request.")
    method = payload.get("method")
    if method == "probe":
        if set(payload) != {"version", "method"}:
            raise ValueError("Invalid IPC probe.")
        return None
    if method != "complete_structured" or set(payload) != {"version", "method", "kwargs"}:
        raise ValueError("Unsupported IPC request.")
    kwargs = payload.get("kwargs")
    if not isinstance(kwargs, dict) or set(kwargs) - _ALLOWED_KWARGS:
        raise ValueError("Invalid structured LLM request.")
    instructions = kwargs.get("instructions")
    items = kwargs.get("input")
    if not isinstance(instructions, str) or len(instructions) > _MAX_PROMPT_CHARS:
        raise ValueError("Invalid structured LLM request.")
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError("Invalid structured LLM request.")
    item = items[0]
    if (
        not isinstance(item, dict)
        or set(item) != {"type", "text"}
        or item.get("type") != "text"
        or not isinstance(item.get("text"), str)
        or len(item["text"]) > _MAX_INPUT_CHARS
    ):
        raise ValueError("Invalid structured LLM request.")
    schema = kwargs.get("json_schema")
    if not isinstance(schema, dict):
        raise ValueError("Invalid structured LLM request.")
    _validate_json_value(schema)
    _encode(schema, _MAX_SCHEMA_BYTES)
    if "schema_name" in kwargs and (not isinstance(kwargs["schema_name"], str) or len(kwargs["schema_name"]) > 128):
        raise ValueError("Invalid structured LLM request.")
    if "system_prompt" in kwargs and (
        not isinstance(kwargs["system_prompt"], str) or len(kwargs["system_prompt"]) > _MAX_PROMPT_CHARS
    ):
        raise ValueError("Invalid structured LLM request.")
    if not isinstance(kwargs.get("purpose"), str) or len(kwargs["purpose"]) > 128:
        raise ValueError("Invalid structured LLM request.")
    if "json_mode" in kwargs and not isinstance(kwargs["json_mode"], bool):
        raise ValueError("Invalid structured LLM request.")
    if "temperature" in kwargs and (
        isinstance(kwargs["temperature"], bool)
        or not isinstance(kwargs["temperature"], (int, float))
        or not math.isfinite(kwargs["temperature"])
        or not 0 <= kwargs["temperature"] <= 2
    ):
        raise ValueError("Invalid structured LLM request.")
    if "model" in kwargs and (not isinstance(kwargs["model"], str) or len(kwargs["model"]) > 256):
        raise ValueError("Invalid structured LLM request.")
    return kwargs


def _probe_profile_socket(path: Path) -> bool | None:
    request_data = _encode({"version": _PROTOCOL_VERSION, "method": "probe"}, 1024)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            try:
                connection.connect(str(path))
            except OSError as exc:
                if exc.errno in {errno.ECONNREFUSED, errno.ENOENT}:
                    return None
                return False
            connection.sendall(request_data)
            connection.shutdown(socket.SHUT_WR)
            response_data = _receive_all(connection, 1024)
        response = json.loads(response_data.decode("utf-8"))
    except Exception:
        return False
    return (
        isinstance(response, dict)
        and set(response) == {"ok", "protocol"}
        and response.get("ok") is True
        and type(response.get("protocol")) is int
        and response["protocol"] == _PROTOCOL_VERSION
    )


def _result_payload(result: Any) -> dict[str, Any]:
    parsed = getattr(result, "parsed", None)
    text = getattr(result, "text", "") or ""
    provider = getattr(result, "provider", "") or ""
    model = getattr(result, "model", "") or ""
    if parsed is not None:
        if not isinstance(parsed, dict):
            raise ValueError("Structured response is not an object.")
        _validate_json_value(parsed)
    if not isinstance(text, str) or len(text) > _MAX_RESPONSE_BYTES:
        raise ValueError("Structured response exceeds limits.")
    if not isinstance(provider, str) or len(provider) > 256 or not isinstance(model, str) or len(model) > 256:
        raise ValueError("Structured response exceeds limits.")
    return {
        "ok": True,
        "parsed": parsed,
        "text": text,
        "provider": provider,
        "model": model,
    }


def _same_user_peer(connection: socket.socket) -> bool:
    expected_uid = os.getuid()
    getpeereid = getattr(connection, "getpeereid", None)
    if callable(getpeereid):
        try:
            peer_uid, _ = cast(tuple[int, int], getpeereid())
        except OSError:
            return False
        return peer_uid == expected_uid
    peer_cred = getattr(socket, "SO_PEERCRED", None)
    if peer_cred is not None:
        try:
            credentials = connection.getsockopt(socket.SOL_SOCKET, peer_cred, struct.calcsize("3i"))
            _, peer_uid, _ = struct.unpack("3i", credentials)
        except OSError:
            return False
        return peer_uid == expected_uid
    # On platforms without a peer-credential API, the private 0700 directory
    # and 0600 socket restrict access to the owning OS user.
    return True


class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(_REQUEST_TIMEOUT_SECONDS)
        try:
            if not _same_user_peer(self.request):
                return
            kwargs = _read_request(self.request)
            if kwargs is None:
                data = _encode({"ok": True, "protocol": _PROTOCOL_VERSION}, 1024)
                self.request.sendall(data)
                return
            server = cast(_ThreadedUnixServer, self.server)
            with server.llm_lock:
                result = server.llm.complete_structured(**kwargs)
            response = _result_payload(result)
            data = _encode(response, _MAX_RESPONSE_BYTES)
        except Exception:
            # Provider exception text may contain private prompt or credential data.
            data = b'{"ok":false,"error":"AI request failed."}'
        try:
            self.request.sendall(data)
        except OSError:
            pass


class _ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, path: str, llm: Any):
        self.llm = llm
        self.llm_lock = threading.Lock()
        self.ready = threading.Event()
        super().__init__(path, _RequestHandler, bind_and_activate=True)

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self.ready.set()
        super().serve_forever(poll_interval=poll_interval)


class ProfileLlmServer:
    """Own one private socket listener and remove it when the plugin unloads."""

    def __init__(self, profile_home: Path | str, llm: Any):
        self.profile_home = Path(profile_home).expanduser().resolve()
        self.public_path = self.profile_home / _SOCKET_RELATIVE_PATH
        self.path = profile_socket_path(self.profile_home)
        self._physical_path = self.profile_home / _LONG_HOME_SOCKET_NAME
        self._alias_path = self.path.parent if self.path != self.public_path else None
        self._owns_alias = False
        self._owns_public_link = False
        self.llm = llm
        self._server: _ThreadedUnixServer | None = None
        self._thread: threading.Thread | None = None
        self._socket_identity: tuple[int, int] | None = None

    def start(self) -> "ProfileLlmServer":
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("Local Unix sockets are unavailable.")
        with _profile_endpoint_lock(self.profile_home):
            self._prepare_profile_directory()
            return self._start_locked()

    def _prepare_profile_directory(self) -> None:
        if self.profile_home.is_symlink():
            raise RuntimeError("Magi IPC path must not contain symlinks.")
        parent_info = self.profile_home.stat()
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.getuid()
            or stat.S_IMODE(parent_info.st_mode) & 0o022
        ):
            raise RuntimeError("Magi IPC profile directory is not trusted.")
        data_root = self.public_path.parent.parent
        if data_root.is_symlink():
            raise RuntimeError("Magi IPC path must not contain symlinks.")
        data_root.mkdir(mode=0o700, exist_ok=True)
        data_root_info = data_root.lstat()
        if (
            not stat.S_ISDIR(data_root_info.st_mode)
            or data_root_info.st_uid != os.getuid()
            or stat.S_IMODE(data_root_info.st_mode) & 0o022
        ):
            raise RuntimeError("Magi IPC profile directory is not trusted.")
        self.public_path.parent.mkdir(mode=0o700, exist_ok=True)
        directory_info = self.public_path.parent.lstat()
        if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != os.getuid():
            raise RuntimeError("Magi IPC data directory is not owned by this user.")
        os.chmod(self.public_path.parent, 0o700)

    def _start_locked(self) -> "ProfileLlmServer":
        if self._alias_path is not None:
            runtime_root = self.path.parents[1]
            _ensure_private_runtime_root(runtime_root)
            if self._alias_path.is_symlink():
                if self._alias_path.resolve() != self.profile_home:
                    raise RuntimeError("Magi IPC alias is occupied.")
            elif self._alias_path.exists():
                raise RuntimeError("Magi IPC alias is occupied.")
            else:
                self._alias_path.symlink_to(self.profile_home, target_is_directory=True)
                self._owns_alias = True
        if self._alias_path is not None and (self.public_path.exists() or self.public_path.is_symlink()):
            if not self.public_path.is_symlink() or self.public_path.resolve() != self._physical_path:
                self._cleanup_links()
                raise RuntimeError("Magi IPC public path is occupied.")
        if self.path.exists() or self.path.is_symlink():
            if self.path.is_symlink() or not stat.S_ISSOCK(self.path.lstat().st_mode):
                raise RuntimeError("Magi IPC socket path is occupied.")
            socket_info = self.path.lstat()
            probe_result = _probe_profile_socket(self.path)
            if probe_result is True:
                self._owns_alias = False
                self._owns_public_link = False
                return self
            if probe_result is False:
                self._owns_alias = False
                self._owns_public_link = False
                raise RuntimeError("Magi IPC socket is active but incompatible.")
            try:
                current_info = self.path.lstat()
            except FileNotFoundError:
                current_info = None
            if current_info is not None:
                if (
                    not stat.S_ISSOCK(current_info.st_mode)
                    or (current_info.st_dev, current_info.st_ino)
                    != (socket_info.st_dev, socket_info.st_ino)
                ):
                    raise RuntimeError("Magi IPC socket changed during recovery.")
                self.path.unlink()
        if self._alias_path is not None:
            if self.public_path.is_symlink():
                self._owns_public_link = True
            else:
                target = os.path.relpath(self._physical_path, self.public_path.parent)
                self.public_path.symlink_to(target)
                self._owns_public_link = True
        server = None
        try:
            server = _ThreadedUnixServer(str(self.path), self.llm)
            info = self.path.lstat()
            self._socket_identity = (info.st_dev, info.st_ino)
            os.chmod(self.path, 0o600)
            self._server = server
            self._thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.05),
                name="magi-profile-llm-ipc",
                daemon=True,
            )
            self._thread.start()
            if not server.ready.wait(timeout=1):
                raise RuntimeError("Magi IPC listener did not become ready.")
        except OSError as exc:
            if server is None and self.path.exists() and stat.S_ISSOCK(self.path.lstat().st_mode):
                socket_info = self.path.lstat()
                probe_result = _probe_profile_socket(self.path)
                if probe_result is True:
                    self._owns_alias = False
                    self._owns_public_link = False
                    return self
                if probe_result is False:
                    self._owns_alias = False
                    self._owns_public_link = False
                    raise RuntimeError("Magi IPC socket is active but incompatible.") from None
                try:
                    current_info = self.path.lstat()
                except FileNotFoundError:
                    current_info = None
                if current_info is not None:
                    if (
                        not stat.S_ISSOCK(current_info.st_mode)
                        or (current_info.st_dev, current_info.st_ino)
                        != (socket_info.st_dev, socket_info.st_ino)
                    ):
                        self._cleanup_links()
                        raise RuntimeError("Magi IPC socket changed during recovery.") from None
                    self.path.unlink()
            if server is not None:
                self._close_failed_start(server)
            self._cleanup_links()
            raise exc
        except Exception:
            if server is not None:
                self._close_failed_start(server)
            self._cleanup_links()
            raise
        return self

    def _close_failed_start(self, server: _ThreadedUnixServer) -> None:
        thread, self._thread = self._thread, None
        self._server = None
        if thread is not None and thread.is_alive():
            server.shutdown()
        if thread is not None and thread.ident is not None:
            thread.join()
        server.server_close()
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._socket_identity:
                self.path.unlink()
        except OSError:
            pass
        self._socket_identity = None

    def _cleanup_links(self, *, force: bool = False) -> None:
        if force or self._owns_public_link:
            try:
                if self.public_path.is_symlink() and self.public_path.resolve() == self._physical_path:
                    self.public_path.unlink()
            except OSError:
                pass
            self._owns_public_link = False
        if (force or self._owns_alias) and self._alias_path is not None:
            try:
                if self._alias_path.is_symlink() and self._alias_path.resolve() == self.profile_home:
                    self._alias_path.unlink()
            except OSError:
                pass
            self._owns_alias = False

    def stop(self) -> None:
        with _profile_endpoint_lock(self.profile_home):
            self._stop_locked()

    def _stop_locked(self) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        owns_listener = server is not None
        if server is not None and thread is not None and thread.is_alive():
            server.shutdown()
        if thread is not None and thread.ident is not None:
            thread.join()
        if server is not None:
            server.server_close()
        try:
            if owns_listener:
                info = self.path.lstat()
                if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._socket_identity:
                    self.path.unlink()
        except OSError:
            pass
        self._socket_identity = None
        self._cleanup_links(force=owns_listener)


class ProfileLlmClient:
    """Client exposing only Hermes' structured-completion interface."""

    def __init__(self, profile_home: Path | str, timeout: float = _REQUEST_TIMEOUT_SECONDS):
        self.path = profile_socket_path(profile_home)
        self.timeout = timeout

    @property
    def is_available(self) -> bool:
        try:
            info = self.path.lstat()
            return (
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and _probe_profile_socket(self.path) is True
            )
        except OSError:
            return False

    def complete_structured(self, **kwargs: Any) -> Any:
        request = _encode(
            {"version": _PROTOCOL_VERSION, "method": "complete_structured", "kwargs": kwargs},
            _MAX_REQUEST_BYTES,
        )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(str(self.path))
                connection.sendall(request)
                connection.shutdown(socket.SHUT_WR)
                response_data = _receive_all(connection, _MAX_RESPONSE_BYTES)
            response = json.loads(response_data.decode("utf-8"))
        except Exception:
            raise RuntimeError("Magi Agent LLM bridge is unavailable for this profile.") from None
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError("Magi Agent LLM request failed.")
        if set(response) != {"ok", "parsed", "text", "provider", "model"}:
            raise RuntimeError("Magi Agent LLM request failed.")
        parsed = response["parsed"]
        text = response["text"]
        provider = response["provider"]
        model = response["model"]
        if parsed is not None:
            if not isinstance(parsed, dict):
                raise RuntimeError("Magi Agent LLM request failed.")
            try:
                _validate_json_value(parsed)
            except ValueError:
                raise RuntimeError("Magi Agent LLM request failed.") from None
        if (
            not isinstance(text, str)
            or len(text) > _MAX_RESPONSE_BYTES
            or not isinstance(provider, str)
            or len(provider) > 256
            or not isinstance(model, str)
            or len(model) > 256
        ):
            raise RuntimeError("Magi Agent LLM request failed.")
        return SimpleNamespace(
            parsed=parsed,
            text=text,
            provider=provider,
            model=model,
        )
