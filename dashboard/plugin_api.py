"""Structured backend for Magi.

Hermes mounts this router below ``/api/plugins/magi`` and scopes the
request to the active profile. Pending-write decisions replay Hermes' native
staged-memory semantics directly in the plugin backend, so they are independent
of chat sessions. Stored-memory edits delegate persistence to Hermes' own
``MemoryStore``.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


_PLUGIN_ID = "magi"
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_core = runpy.run_path(str(_PLUGIN_ROOT / "core.py"))
MagiReview = _core["MagiReview"]
resolve_hermes_home = _core["resolve_hermes_home"]
build_memory_compaction_preview = _core["build_memory_compaction_preview"]
memory_source_fingerprint = _core["memory_source_fingerprint"]
has_embedded_entry_delimiter = _core["has_embedded_entry_delimiter"]
_memory_compaction_budgets = _core["_memory_compaction_budgets"]
_memory_compaction_max_entries = _core["_memory_compaction_max_entries"]

router = APIRouter()

_MEMORY_CHARS_PER_TOKEN = 2.75
_TOKEN_ESTIMATE_METHOD = "hermes_memory_budget_2.75_chars_per_token"
_RUNTIME_BRIDGE_PATH = (_PLUGIN_ROOT / "runtime_bridge.py").resolve()


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


def _estimate_tokens(text: str) -> int:
    return int(round(len(text) / _MEMORY_CHARS_PER_TOKEN)) if text else 0


def _source_fingerprint(entries: List[str]) -> str:
    return memory_source_fingerprint(entries)


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
        "entries": [{"index": index, "content": entry} for index, entry in enumerate(entries)],
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
        return {"state": "unknown", "can_apply": True, "missing_count": 0, "destructive_count": 0}

    entries = store.memory_entries if target == "memory" else store.user_entries
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
    try:
        store = _memory_store()
    except Exception:
        return {
            record.id: {
                "state": "unknown",
                "can_apply": True,
                "missing_count": 0,
                "destructive_count": 0,
            }
            for record in records
        }
    return {record.id: _record_target_status(record, store) for record in records}


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
    store = _memory_store() if action == "approve" else None
    for record in records:
        if action == "reject":
            try:
                record.path.unlink()
                rejected += 1
                complete += 1
                items.append({"id": record.id, "status": "rejected"})
            except Exception as exc:
                error = f"Pending proposal removal failed ({type(exc).__name__})."
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
        overwritten.extend(f"  {record.id}: {text}" for text in _changed_entries(result, "replaced"))
        removed.extend(f"  {record.id}: {text}" for text in _changed_entries(result, "removed"))
        try:
            record.path.unlink()
            complete += 1
            items.append({"id": record.id, "status": "applied"})
        except Exception as exc:
            error = f"Memory applied, but pending proposal cleanup failed ({type(exc).__name__}). Reconcile before retrying."
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
    return {
        "success": outcome == "complete",
        "outcome": outcome,
        "attempted": len(records),
        "applied": applied,
        "rejected": rejected,
        "failed": failed,
        "items": items,
        "output": output,
    }


@router.get("/memory")
def stored_memory() -> Dict[str, Any]:
    store = _memory_store()
    return {
        "targets": {
            "memory": _memory_target(store, "memory"),
            "user": _memory_target(store, "user"),
        }
    }


@router.put("/memory/{target}")
def replace_stored_memory(target: str, body: MemoryEditRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")
    _reject_embedded_delimiter(body.content)

    store = _memory_store()
    result = store.replace(target, body.old_text, body.content, matched_entry=body.old_text)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "target": _memory_target(store, target),
    }


@router.post("/memory/{target}/entries")
def add_stored_memory(target: str, body: MemoryAddRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")
    _reject_embedded_delimiter(body.content)

    store = _memory_store()
    result = store.add(target, body.content)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "target": _memory_target(store, target),
    }


@router.delete("/memory/{target}/entries")
def delete_stored_memory(target: str, body: MemoryDeleteRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")

    store = _memory_store()
    result = store.remove(target, body.old_text, matched_entry=body.old_text)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "target": _memory_target(store, target),
    }


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
    result = store.apply_batch(target, operations)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "target": _memory_target(store, target),
    }
