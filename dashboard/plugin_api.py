"""Structured backend for Magi.

Hermes mounts this router below ``/api/plugins/magi`` and scopes the
request to the active profile. Pending-write decisions replay Hermes' native
staged-memory semantics directly in the plugin backend, so they are independent
of chat sessions. Stored-memory edits delegate persistence to Hermes' own
``MemoryStore``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
import secrets
import sys
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StrictBool, StrictInt


_PLUGIN_ID = "magi"
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_core = runpy.run_path(str(_PLUGIN_ROOT / "core.py"))
MagiReview = _core["MagiReview"]
resolve_hermes_home = _core["resolve_hermes_home"]
build_memory_compaction_preview = _core["build_memory_compaction_preview"]
build_memory_organization_analysis = _core["build_memory_organization_analysis"]
memory_source_fingerprint = _core["memory_source_fingerprint"]
has_embedded_entry_delimiter = _core["has_embedded_entry_delimiter"]
_memory_compaction_budgets = _core["_memory_compaction_budgets"]
_memory_compaction_max_entries = _core["_memory_compaction_max_entries"]

router = APIRouter()

_MEMORY_CHARS_PER_TOKEN = 2.75
_TOKEN_ESTIMATE_METHOD = "hermes_memory_budget_2.75_chars_per_token"
_MEMORY_IMPORT_MAX_BYTES = 1_000_000
_MEMORY_IMPORT_MAX_ENTRIES = 1_000
_HISTORY_MAX_RECORDS = 500
_HISTORY_MAX_BYTES = 256_000
_HISTORY_LOCK = threading.Lock()
_RUNTIME_BRIDGE_PATH = (_PLUGIN_ROOT / "runtime_bridge.py").resolve()
_PROCESS_APPLIED_MARKERS: set[str] = set()


class MemoryEditRequest(BaseModel):
    old_text: str
    content: str


class MemoryAddRequest(BaseModel):
    content: str


class MemoryDeleteRequest(BaseModel):
    old_text: str


class MemoryCompactionApplyRequest(BaseModel):
    source_fingerprint: str
    entries: List[str]


class MemoryImportPreviewRequest(BaseModel):
    format_version: StrictInt
    target: str
    entries: Any


class MemoryImportApplyRequest(MemoryImportPreviewRequest):
    source_fingerprints: Dict[str, str]
    confirmed: bool = False


class HistoryClearRequest(BaseModel):
    confirmed: StrictBool = False


class PendingDecisionRequest(BaseModel):
    action: str


def _settings() -> Dict[str, Any]:
    """Mirror PluginContext.get_config for this profile-scoped backend."""
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
    except Exception:
        return {}

    plugins = config.get("plugins") if isinstance(config, dict) else None
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    entry = entries.get(_PLUGIN_ID) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return {}

    legacy = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    settings = entry.get("settings") if isinstance(entry.get("settings"), dict) else {}
    return {**legacy, **settings}


def _review() -> Any:
    settings = _settings()
    home = resolve_hermes_home(
        override=str(settings.get("home_override") or ""),
    )
    return MagiReview(
        home=home,
        default_page_size=max(1, int(settings.get("default_page_size") or 20)),
        max_page_size=max(1, int(settings.get("max_page_size") or 100)),
    )


def _memory_store() -> Any:
    """Load the active profile's built-in memory with Hermes' native safeguards."""
    from tools.memory_tool import load_on_disk_store

    return load_on_disk_store()


def _apply_pending_memory(payload: Dict[str, Any], store: Any) -> Dict[str, Any]:
    """Replay one approved staged write through Hermes' native pinned-entry semantics."""
    from tools.memory_tool import apply_memory_pending

    return apply_memory_pending(payload, store)


def _changed_entries(result: Dict[str, Any], kind: str) -> List[str]:
    """Mirror Hermes' approval output for whole entries replaced or removed."""
    single = result.get(f"{kind}_entry")
    batch = result.get(f"{kind}_entries") or {}
    return ([single] if single else []) + [batch[key] for key in sorted(batch, key=int)]


def _entry_delimiter() -> str:
    try:
        from tools.memory_tool_store import ENTRY_DELIMITER

        return ENTRY_DELIMITER
    except ModuleNotFoundError:
        return "\n§\n"


def _serialize_entries(entries: List[str]) -> str:
    return _entry_delimiter().join(entries)


def _reject_embedded_delimiter(content: str) -> None:
    if has_embedded_entry_delimiter(content, _entry_delimiter()):
        raise HTTPException(
            status_code=400,
            detail="Memory entry contains the Hermes entry delimiter; split or edit this entry before saving.",
        )


def _pending_delimiter_error(payload: Dict[str, Any]) -> str | None:
    operations = payload.get("operations") if payload.get("action") == "batch" else [payload]
    if not isinstance(operations, list):
        return None
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if operation.get("action") in {"add", "replace"}:
            content = operation.get("content")
            if isinstance(content, str) and has_embedded_entry_delimiter(content, _entry_delimiter()):
                return "proposal contains the Hermes entry delimiter; reject it and recreate the change"
    return None


def _reconciliation_path(review: Any, record: Any) -> Path:
    """Bind a content-free marker to the profile, pending path and exact record."""
    record_bytes = json.dumps(
        record.data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    key = hashlib.sha256(
        str(record.path.resolve()).encode("utf-8") + b"\0" + record_bytes
    ).hexdigest()
    return review.home / "plugins" / "magi" / "reconciliation" / f"{key}.json"


def _requires_reconciliation(review: Any, record: Any) -> bool:
    path = _reconciliation_path(review, record)
    return str(path) in _PROCESS_APPLIED_MARKERS or path.exists()


def _mark_applied(review: Any, record: Any) -> str | None:
    """Write best-effort metadata before attempting to remove the pending file."""
    path = _reconciliation_path(review, record)
    _PROCESS_APPLIED_MARKERS.add(str(path))
    temp = path.with_name(path.name + f".{secrets.token_hex(8)}.tmp")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with temp.open("x", encoding="utf-8") as stream:
            json.dump({"state": "applied_pending_cleanup", "schema_version": 1}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        return None
    except OSError:
        return "Reconciliation marker could not be persisted."
    finally:
        temp.unlink(missing_ok=True)


def _clear_applied(review: Any, record: Any) -> None:
    path = _reconciliation_path(review, record)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # A stale marker cannot reapply a proposal whose file is gone.
    _PROCESS_APPLIED_MARKERS.discard(str(path))


def _estimate_tokens(text: str) -> int:
    return int(round(len(text) / _MEMORY_CHARS_PER_TOKEN)) if text else 0


def _source_fingerprint(entries: List[str]) -> str:
    return memory_source_fingerprint(entries)


def _memory_import_plans(store: Any, body: MemoryImportPreviewRequest) -> Dict[str, Dict[str, Any]]:
    if body.format_version != 1:
        raise HTTPException(status_code=400, detail="Unsupported memory export format_version.")
    if body.target not in {"memory", "user", "both"}:
        raise HTTPException(status_code=400, detail="target must be 'memory', 'user', or 'both'.")
    if body.target == "both":
        if not isinstance(body.entries, dict) or set(body.entries) != {"memory", "user"}:
            raise HTTPException(status_code=400, detail="Both-target imports require memory and user entry arrays.")
        imported = body.entries
    else:
        if not isinstance(body.entries, list):
            raise HTTPException(status_code=400, detail="entries must be an array for a single target.")
        imported = {body.target: body.entries}
    if len(json.dumps(body.model_dump(), ensure_ascii=False).encode("utf-8")) > _MEMORY_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Memory import exceeds the 1 MB limit.")

    plans = {}
    delimiter = _entry_delimiter()
    for target, entries in imported.items():
        if not isinstance(entries, list) or len(entries) > _MEMORY_IMPORT_MAX_ENTRIES:
            raise HTTPException(status_code=400, detail="Each imported target must contain at most 1000 entries.")
        if any(not isinstance(entry, str) or not entry.strip() for entry in entries):
            raise HTTPException(status_code=400, detail="Imported entries must be non-empty strings.")
        if any(delimiter in entry for entry in entries):
            raise HTTPException(status_code=400, detail="Imported entry contains the Hermes memory delimiter.")
        if len(set(entries)) != len(entries):
            raise HTTPException(status_code=400, detail="Imported entries must not contain duplicates.")
        current = list(store.memory_entries if target == "memory" else store.user_entries)
        additions = [entry for entry in entries if entry not in current]
        if len(delimiter.join([*current, *additions])) > int(store._char_limit(target)):
            raise HTTPException(status_code=400, detail=f"Import exceeds the {target} character budget.")
        plans[target] = {
            "source_fingerprint": _source_fingerprint(current),
            "existing_count": len(current),
            "skipped_count": len(entries) - len(additions),
            "additions": additions,
        }
    return plans


def _history_path() -> Path:
    home = resolve_hermes_home(override=str(_settings().get("home_override") or ""))
    return home / "plugin-data" / _PLUGIN_ID / "history.jsonl"


def _read_history(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        if not isinstance(record.get("id"), str) or not re.fullmatch(r"[0-9a-f]{16}", record["id"]):
            continue
        if record.get("operation") not in {"approve", "reject", "add", "edit", "delete", "compact", "import"}:
            continue
        if record.get("target") not in {"memory", "user", "multiple"}:
            continue
        if record.get("result") not in {"success", "failure", "partial"}:
            continue
        if record.get("error") not in {None, "operation_failed"}:
            continue
        if not isinstance(record.get("timestamp"), str):
            continue
        try:
            parsed_timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != timezone.utc.utcoffset(parsed_timestamp):
            continue
        records.append({
            "id": record["id"],
            "timestamp": record["timestamp"],
            "operation": record["operation"],
            "target": record["target"],
            "result": record["result"],
            "error": record["error"],
        })
    return records


def _record_history(operation: str, target: str, result: str) -> bool:
    record = {
        "id": uuid.uuid4().hex[:16],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "target": target,
        "result": result,
        "error": "operation_failed" if result == "failure" else None,
    }
    temporary = None
    try:
        path = _history_path()
        temporary = path.with_name(f"{path.name}.{record['id']}.tmp")
        with _HISTORY_LOCK:
            records = _read_history(path)
            records.append(record)
            records = records[-_HISTORY_MAX_RECORDS:]
            payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
            while records and len(payload.encode("utf-8")) > _HISTORY_MAX_BYTES:
                records.pop(0)
                payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(path.parent, 0o700)
            temporary.write_text(payload, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        return True
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _audit_result(result: Dict[str, Any], operation: str, target: str) -> Dict[str, Any]:
    outcome = "partial" if result.get("partial") else "success" if result.get("success") else "failure"
    recorded = _record_history(operation, target, outcome)
    result["audit_recorded"] = recorded
    if not recorded:
        result["audit_warning"] = "The operation completed, but its local audit record could not be saved."
    return result


def _plugin_llm() -> Any:
    """Find the live plugin-owned bridge without writing to the import registry.

    Hermes loads Agent and dashboard under different module names. Inspect the
    loaded Agent's module by its exact file, without relying on the host's
    synthetic package naming convention or installing a process-global alias.
    """
    active = []
    for module in tuple(sys.modules.values()):
        if not getattr(module, "__name__", "").endswith(".runtime_bridge"):
            continue
        filename = getattr(module, "__file__", None)
        if not filename or Path(filename).resolve() != _RUNTIME_BRIDGE_PATH:
            continue
        try:
            active.append(module.get_plugin_llm())
        except RuntimeError:
            continue  # Context cleared by the Agent's on_unload callback.
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise RuntimeError("Multiple Magi Agent contexts are loaded in this plugin host.")
    raise RuntimeError(
        "Magi Agent context is unavailable; enable the Agent plugin for this profile."
    )


def _memory_diagnostics(entries: List[str], used_chars: int, char_limit: int) -> Dict[str, Any]:
    """Linear-time, exact-text indicators; no inference or memory writes."""
    distribution = {"empty": 0, "1-120": 0, "121-500": 0, "501-1000": 0, ">1000": 0}
    positions: Dict[str, List[int]] = {}
    long_indices = []
    for index, entry in enumerate(entries):
        size = len(entry)
        if size == 0:
            bucket = "empty"
        elif size <= 120:
            bucket = "1-120"
        elif size <= 500:
            bucket = "121-500"
        elif size <= 1000:
            bucket = "501-1000"
        else:
            bucket = ">1000"
        distribution[bucket] += 1
        if size > 500:
            long_indices.append(index)
        positions.setdefault(entry, []).append(index)
    return {
        "method": "deterministic_text_only_v1",
        "entry_count": len(entries),
        "used_chars": used_chars,
        "remaining_chars": max(0, char_limit - used_chars),
        "over_budget_chars": max(0, used_chars - char_limit),
        "long_entry_threshold_chars": 500,
        "long_entry_indices": long_indices,
        "exact_duplicate_groups": [indices for indices in positions.values() if len(indices) > 1],
        "size_distribution": distribution,
    }


def _memory_target(store: Any, target: str) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")
    entries = store.memory_entries if target == "memory" else store.user_entries
    used_chars = int(store._char_count(target))
    char_limit = int(store._char_limit(target))
    result = {
        "target": target,
        "count": len(entries),
        "used_chars": used_chars,
        "char_limit": char_limit,
        "usage_percent": round((used_chars / char_limit) * 100, 1) if char_limit > 0 else 0.0,
        "estimated_tokens": int(round(used_chars / _MEMORY_CHARS_PER_TOKEN)),
        "estimated_token_limit": int(round(char_limit / _MEMORY_CHARS_PER_TOKEN)),
        "token_estimate_method": _TOKEN_ESTIMATE_METHOD,
        "source_fingerprint": _source_fingerprint(list(entries)),
        "entries": [{"index": index, "content": entry} for index, entry in enumerate(entries)],
        "diagnostics": _memory_diagnostics(entries, used_chars, char_limit),
    }
    issues = [
        index for index, entry in enumerate(entries)
        if has_embedded_entry_delimiter(entry, _entry_delimiter())
    ]
    if issues:
        result["integrity_issues"] = issues
    return result


def _record_target_status(record: Any, store: Any) -> Dict[str, Any]:
    """Report whether pinned replace/remove targets still exist in live memory."""
    payload = record.payload
    target = payload.get("target")
    if target not in {"memory", "user"}:
        return {
            "state": "unknown", "can_apply": False,
            "missing_count": 0, "destructive_count": 0,
            "message": "Proposal target cannot be verified; reject and recreate it.",
        }

    entries = store.memory_entries if target == "memory" else store.user_entries
    if payload.get("action") == "add" and payload.get("content") in entries:
        return {
            "state": "ambiguous", "can_apply": False,
            "missing_count": 0, "destructive_count": 0,
            "message": "This content is already stored. The pending add may have been applied; reconcile or reject it before retrying.",
        }
    operations = payload.get("operations") if payload.get("action") == "batch" else [payload]
    operations = operations if isinstance(operations, list) else []
    destructive = [
        operation
        for operation in operations
        if isinstance(operation, dict) and operation.get("action") in {"replace", "remove"}
    ]
    missing = [
        operation
        for operation in destructive
        if operation.get("matched_entry") and operation.get("matched_entry") not in entries
    ]
    unpinned = [operation for operation in destructive if not operation.get("matched_entry")]

    if missing:
        count = len(missing)
        noun = "entry" if count == 1 else "entries"
        return {
            "state": "missing",
            "can_apply": False,
            "missing_count": count,
            "destructive_count": len(destructive),
            "message": (
                f"{count} pinned target {noun} no longer exists in stored {target} memory. "
                "This pending write is obsolete; reject it to delete the proposal."
            ),
        }
    if unpinned:
        return {
            "state": "unverifiable",
            "can_apply": False,
            "missing_count": 0,
            "destructive_count": len(destructive),
            "message": (
                "This destructive pending write has no pinned target and cannot be verified safely; "
                "reject it and recreate the change."
            ),
        }
    return {
        "state": "ready" if destructive else "not_applicable",
        "can_apply": True,
        "missing_count": 0,
        "destructive_count": len(destructive),
    }


def _record_summary(record: Any, target_status: Dict[str, Any] | None = None) -> Dict[str, Any]:
    summary = {
        "id": record.id,
        "action": record.action,
        "target": record.target,
        "origin": record.origin,
        "summary": record.summary,
        "created_at": record.created_at,
    }
    if target_status is not None:
        summary["target_status"] = target_status
    return summary


def _target_status_for_records(records: List[Any]) -> Dict[str, Dict[str, Any]]:
    review = _review()
    try:
        store = _memory_store()
    except Exception:
        return {
            record.id: {
                "state": "reconciliation_required" if _requires_reconciliation(review, record) else "unknown",
                "can_apply": False,
                "missing_count": 0,
                "destructive_count": 0,
                "message": (
                    "Memory may already have been applied; reconcile or reject this pending proposal."
                    if _requires_reconciliation(review, record)
                    else "Stored memory is unavailable for verification. Approval is disabled; rejection remains possible."
                ),
            }
            for record in records
        }
    statuses = {}
    for record in records:
        errors, _warnings = review.validate_record(record)
        if _requires_reconciliation(review, record):
            statuses[record.id] = {
                "state": "reconciliation_required", "can_apply": False,
                "missing_count": 0, "destructive_count": 0,
                "message": "Memory was applied but proposal cleanup was uncertain. Reconcile the current memory and reject this proposal; do not approve again.",
            }
        elif errors:
            statuses[record.id] = {
                "state": "unverifiable", "can_apply": False,
                "missing_count": 0, "destructive_count": 0,
                "message": "Pending proposal has invalid structure; reject and recreate it.",
            }
        else:
            statuses[record.id] = _record_target_status(record, store)
    return statuses


@router.get("/records")
def list_records() -> Dict[str, Any]:
    review = _review()
    records, issues = review._load()
    statuses = _target_status_for_records(records)
    return {
        "count": len(records),
        "records": [_record_summary(record, statuses[record.id]) for record in reversed(records)],
        "issues": issues,
    }


@router.get("/records/{selector}")
def record_detail(selector: str) -> Dict[str, Any]:
    review = _review()
    record, error = review._resolve(selector)
    if error:
        if error == "No pending memory writes.":
            error = f"No pending memory write with id/prefix {selector!r}."
        raise HTTPException(status_code=404, detail=error)
    assert record is not None
    target_status = _target_status_for_records([record])[record.id]
    return {
        "id": record.id,
        "record": _record_summary(record, target_status),
        "payload": record.payload,
        "proposal": review.show(record.id),
        "diff": review.diff(record.id),
        "raw": review.raw(record.id),
        "verify": review.verify(record.id),
    }


@router.post("/records/{selector}/decision")
def decide_pending_record(selector: str, body: PendingDecisionRequest) -> Dict[str, Any]:
    action = body.action.strip().lower()
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'.")

    review = _review()
    if selector == "all":
        records, _issues = review._load()
        if not records:
            return {
                "success": True, "outcome": "complete", "attempted": 0,
                "applied": 0, "rejected": 0, "failed": 0,
                "items": [], "output": "No pending memory writes.",
            }
    else:
        record, error = review._resolve(selector)
        if error:
            raise HTTPException(status_code=404, detail=error)
        assert record is not None
        records = [record]

    items: List[Dict[str, Any]] = []
    applied = 0
    rejected = 0
    failed = 0
    complete = 0
    errors = []
    overwritten = []
    removed = []
    store = None
    if action == "approve":
        try:
            store = _memory_store()
        except Exception:
            pass
    for record in records:
        if action == "reject":
            try:
                record.path.unlink()
                _clear_applied(review, record)
                rejected += 1
                complete += 1
                items.append({"id": record.id, "status": "rejected"})
            except Exception as exc:
                error = f"Pending proposal removal failed ({type(exc).__name__})."
                failed += 1
                errors.append(f"{record.id}: {error}")
                items.append({"id": record.id, "status": "failed", "error": error})
            continue

        if _requires_reconciliation(review, record):
            error = "This proposal may already have been applied; reconcile or reject it before retrying."
            failed += 1
            errors.append(f"{record.id}: {error}")
            items.append({"id": record.id, "status": "failed", "error": error})
            continue

        validation_errors, _warnings = review.validate_record(record)
        if validation_errors:
            error = "Pending proposal has invalid structure; reject and recreate it."
        elif store is None:
            error = "Stored memory is unavailable for approval verification."
        else:
            status = _record_target_status(record, store)
            error = status.get("message") or "Pending proposal cannot be verified." if not status["can_apply"] else None
        if error:
            failed += 1
            errors.append(f"{record.id}: {error}")
            items.append({"id": record.id, "status": "failed", "error": error})
            continue

        delimiter_error = _pending_delimiter_error(record.payload)
        if delimiter_error:
            failed += 1
            errors.append(f"{record.id}: {delimiter_error}")
            items.append({"id": record.id, "status": "failed", "error": delimiter_error})
            continue
        try:
            result = _apply_pending_memory(record.payload, store)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if not result.get("success"):
            error = str(result.get("error") or "approval failed")
            failed += 1
            errors.append(f"{record.id}: {error}")
            items.append({"id": record.id, "status": "failed", "error": error})
            continue

        applied += 1
        marker_error = _mark_applied(review, record)
        overwritten.extend(f"  {record.id}: {text}" for text in _changed_entries(result, "replaced"))
        removed.extend(f"  {record.id}: {text}" for text in _changed_entries(result, "removed"))
        try:
            record.path.unlink()
            _clear_applied(review, record)
            complete += 1
            items.append({"id": record.id, "status": "applied"})
        except Exception as exc:
            error = (
                f"Memory applied, but pending proposal cleanup failed ({type(exc).__name__}). "
                "Reconcile before retrying."
                + (f" {marker_error}" if marker_error else "")
            )
            failed += 1
            errors.append(f"{record.id}: {error}")
            items.append({"id": record.id, "status": "applied_cleanup_failed", "error": error})

    if action == "reject" and selector != "all" and rejected == 1:
        output = f"Rejected pending memory write '{records[0].id}'."
    elif action == "reject":
        output = f"Rejected {rejected} pending memory write(s)."
    else:
        output = f"Approved {applied} memory write(s)."
    if overwritten:
        output += "\nOverwrote entire entry (re-add anything you still need):\n" + "\n".join(overwritten)
    if removed:
        output += "\nRemoved entry (re-add anything you still need):\n" + "\n".join(removed)
    if errors:
        output += "\nFailed:\n" + "\n".join(f"  {item}" for item in errors)
    outcome = "complete" if failed == 0 else ("partial" if complete else "failed")
    return _audit_result({
        "success": outcome == "complete",
        "outcome": outcome,
        "attempted": len(records),
        "applied": applied,
        "rejected": rejected,
        "failed": failed,
        "items": items,
        "output": output,
    }, action, "multiple" if len(records) > 1 else str(records[0].payload.get("target") or "memory"))


@router.get("/history")
def get_history(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    operation: Optional[str] = None,
    result: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    try:
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
        if start and end and start > end:
            raise HTTPException(status_code=400, detail="date_from must not be later than date_to.")
        records = _read_history(_history_path())
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="History dates must use YYYY-MM-DD.") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Local Magi history is unavailable.") from exc
    if result not in {None, "success", "failure", "partial"}:
        raise HTTPException(status_code=400, detail="result must be success, failure, or partial.")
    filtered = []
    for record in records:
        try:
            event_day = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).date()
        except (ValueError, TypeError, KeyError):
            continue
        if (start and event_day < start) or (end and event_day > end):
            continue
        if operation and record["operation"] != operation:
            continue
        if result and record["result"] != result:
            continue
        filtered.append(record)
    return {
        "records": list(reversed(filtered[-limit:])),
        "retention": {"max_records": _HISTORY_MAX_RECORDS, "max_bytes": _HISTORY_MAX_BYTES},
    }


@router.delete("/history")
def clear_history(body: HistoryClearRequest) -> Dict[str, Any]:
    if body.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required to clear history.")
    try:
        with _HISTORY_LOCK:
            _history_path().unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Local Magi history could not be cleared.") from exc
    return {"success": True}



@router.get("/memory")
def stored_memory() -> Dict[str, Any]:
    store = _memory_store()
    return {
        "targets": {
            "memory": _memory_target(store, "memory"),
            "user": _memory_target(store, "user"),
        }
    }


@router.post("/memory/import/preview")
def preview_memory_import(body: MemoryImportPreviewRequest) -> Dict[str, Any]:
    plans = _memory_import_plans(_memory_store(), body)
    response = {
        "success": True,
        "target": body.target,
        "source_fingerprints": {target: plan["source_fingerprint"] for target, plan in plans.items()},
        "targets": plans,
    }
    if body.target != "both":
        response.update(plans[body.target])
    return response


@router.post("/memory/import")
def apply_memory_import(body: MemoryImportApplyRequest) -> Dict[str, Any]:
    if body.confirmed is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before importing.")
    store = _memory_store()
    plans = _memory_import_plans(store, body)
    if set(body.source_fingerprints) != set(plans):
        raise HTTPException(status_code=400, detail="Import preview is incomplete; create a new preview.")
    if any(body.source_fingerprints[target] != plan["source_fingerprint"] for target, plan in plans.items()):
        raise HTTPException(status_code=409, detail="Stored memory changed after preview; review a fresh import preview.")

    outcomes = {}
    for target, plan in plans.items():
        if not plan["additions"]:
            outcomes[target] = {"status": "unchanged", "added_count": 0}
        else:
            try:
                result = store.apply_batch(target, [
                    {"action": "add", "content": entry} for entry in plan["additions"]
                ])
            except Exception:
                result = {"success": False, "error": "Hermes could not apply this target's batch."}
            if result.get("success"):
                outcomes[target] = {"status": "applied", "added_count": len(plan["additions"])}
            else:
                outcomes[target] = {"status": "failed", "error": result.get("error", "Import failed.")}
    succeeded = sum(value["status"] in {"applied", "unchanged"} for value in outcomes.values())
    failed = any(value["status"] == "failed" for value in outcomes.values())
    audit_result = "partial" if failed and succeeded else "failure" if failed else "success"
    for target, outcome in outcomes.items():
        outcome["audit_recorded"] = _record_history("import", target, audit_result)
        if not outcome["audit_recorded"]:
            outcome["audit_warning"] = "Magi could not save this target's local audit record."
    return {
        "success": not failed,
        "partial": failed and succeeded > 0,
        "outcomes": outcomes,
        "targets": {target: _memory_target(store, target) for target in outcomes},
    }


@router.put("/memory/{target}")
def replace_stored_memory(target: str, body: MemoryEditRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")
    _reject_embedded_delimiter(body.content)

    store = _memory_store()
    result = _audit_result(store.replace(target, body.old_text, body.content, matched_entry=body.old_text), "edit", target)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "audit_recorded": result["audit_recorded"],
        "audit_warning": result.get("audit_warning"),
        "target": _memory_target(store, target),
    }


@router.post("/memory/{target}/entries")
def add_stored_memory(target: str, body: MemoryAddRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")
    _reject_embedded_delimiter(body.content)

    store = _memory_store()
    result = _audit_result(store.add(target, body.content), "add", target)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "audit_recorded": result["audit_recorded"],
        "audit_warning": result.get("audit_warning"),
        "target": _memory_target(store, target),
    }


@router.delete("/memory/{target}/entries")
def delete_stored_memory(target: str, body: MemoryDeleteRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")

    store = _memory_store()
    result = _audit_result(store.remove(target, body.old_text, matched_entry=body.old_text), "delete", target)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "audit_recorded": result["audit_recorded"],
        "audit_warning": result.get("audit_warning"),
        "target": _memory_target(store, target),
    }


@router.post("/memory/{target}/organization/analyze")
def analyze_memory_organization(target: str) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        return {"success": False, "error": "target must be 'memory' or 'user'."}
    try:
        store = _memory_store()
        entries = list(store.memory_entries if target == "memory" else store.user_entries)
        if not entries or len(entries) > _core["MEMORY_ORGANIZATION_MAX_ENTRIES"] or len(_entry_delimiter().join(entries)) > _core["MEMORY_ORGANIZATION_MAX_CHARS"]:
            return build_memory_organization_analysis(None, target, entries, _entry_delimiter())
        return build_memory_organization_analysis(_plugin_llm(), target, entries, _entry_delimiter())
    except Exception:
        return {"success": False, "error": "AI organization analysis failed; memory was not changed."}


@router.post("/memory/{target}/compact/preview")
def preview_memory_compaction(target: str) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        return {"success": False, "error": "target must be 'memory' or 'user'."}

    store = _memory_store()
    entries = list(store.memory_entries if target == "memory" else store.user_entries)
    try:
        llm = _plugin_llm()
    except Exception as exc:
        return {"success": False, "error": f"AI compaction unavailable: {exc}"}
    model = str(_settings().get("compaction_model") or "").strip() or None
    return build_memory_compaction_preview(llm, target, entries, _entry_delimiter(), model=model)


@router.post("/memory/{target}/compact")
def apply_memory_compaction(target: str, body: MemoryCompactionApplyRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")

    proposed = [entry.strip() for entry in body.entries]
    if not proposed or any(not entry for entry in proposed):
        raise HTTPException(status_code=400, detail="entries must contain at least one non-empty memory entry.")
    for entry in proposed:
        _reject_embedded_delimiter(entry)
    if len(set(proposed)) != len(proposed):
        raise HTTPException(status_code=400, detail="entries must not contain duplicates.")
    if any(_entry_delimiter() in entry for entry in proposed):
        raise HTTPException(status_code=400, detail="entries must not contain the native memory-entry delimiter.")

    store = _memory_store()
    current = list(store.memory_entries if target == "memory" else store.user_entries)
    if not current:
        raise HTTPException(status_code=409, detail={"success": False, "error": "Stored memory is empty; there is nothing to compact."})
    if _source_fingerprint(current) != body.source_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "success": False,
                "error": "Stored memory changed after this compaction preview was generated. Refresh and create a new preview.",
            },
        )

    # The HTTP client supplies these entries, so enforce the same invariants
    # that the preview builder checks before accepting an AI proposal.
    max_entries = _memory_compaction_max_entries(len(current))
    if len(proposed) > max_entries:
        raise HTTPException(status_code=400, detail=f"Compaction must contain at most {max_entries} entries.")
    delimiter = _entry_delimiter()
    source_chars = len(delimiter.join(current))
    proposed_chars = len(delimiter.join(proposed))
    max_chars, _ = _memory_compaction_budgets(source_chars, 1)
    if proposed_chars >= source_chars or proposed_chars > max_chars:
        raise HTTPException(
            status_code=400,
            detail=f"Compaction must reduce memory to at most {max_chars} characters and be shorter than the original.",
        )

    operations = [
        {"action": "remove", "old_text": entry, "matched_entry": entry}
        for entry in current
    ] + [
        {"action": "add", "content": entry}
        for entry in proposed
    ]
    result = _audit_result(store.apply_batch(target, operations), "compact", target)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "audit_recorded": result["audit_recorded"],
        "audit_warning": result.get("audit_warning"),
        "target": _memory_target(store, target),
    }
