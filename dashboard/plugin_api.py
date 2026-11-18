"""Structured backend for Magi.

Hermes mounts this router below ``/api/plugins/magi`` and scopes the
request to the active profile. Pending-write decisions replay Hermes' native
staged-memory semantics directly in the plugin backend, so they are independent
of chat sessions. Stored-memory edits delegate persistence to Hermes' own
``MemoryStore``.
"""

from __future__ import annotations

import inspect
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
_memory_compaction_budgets = _core["_memory_compaction_budgets"]
_memory_compaction_max_entries = _core["_memory_compaction_max_entries"]

router = APIRouter()

_MEMORY_CHARS_PER_TOKEN = 2.75
_TOKEN_ESTIMATE_METHOD = "hermes_memory_budget_2.75_chars_per_token"


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


class LlmSettingsRequest(BaseModel):
    provider: str = ""
    model: str = ""


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


def _estimate_tokens(text: str) -> int:
    return int(round(len(text) / _MEMORY_CHARS_PER_TOKEN)) if text else 0


def _source_fingerprint(entries: List[str]) -> str:
    return memory_source_fingerprint(entries)


def _apply_compaction_batch(store: Any, target: str, operations: List[Dict[str, str]],
                            expected_entries: List[str]) -> Dict[str, Any]:
    """Apply only against the exact reviewed snapshot under Hermes' native lock.

    The public conditional batch API checks the complete ordered source after
    loading disk under its exclusive file lock. Older Hermes versions lack this
    precondition; refuse the write rather than weakening stale-preview safety.
    """
    try:
        supports_precondition = "expected_entries" in inspect.signature(store.apply_batch).parameters
    except (AttributeError, TypeError, ValueError):
        supports_precondition = False
    if not supports_precondition:
        return {
            "success": False,
            "error": "This Hermes version does not support conditional memory batches. Upgrade Hermes before applying compaction.",
        }
    return store.apply_batch(target, operations, expected_entries=expected_entries)


def _plugin_llm() -> Any:
    """Find the Agent-owned bridge without altering the Hermes module registry.

    Hermes loads the Agent and Desktop backend under separate module names in
    the same profile host. The bridge has one owner: the Agent's package.
    Require an unambiguous module with the exact file path for this plugin;
    never select another profile or an unrelated plugin by name alone.
    """
    return _plugin_context().llm


def _plugin_context() -> Any:
    bridge_path = (_PLUGIN_ROOT / "runtime_bridge.py").resolve()
    bridges = [
        module for name, module in tuple(sys.modules.items())
        if name.endswith(".runtime_bridge")
        and getattr(module, "__file__", None)
        and Path(module.__file__).resolve() == bridge_path
    ]
    if len(bridges) != 1:
        raise RuntimeError(
            "Magi Agent context is unavailable or ambiguous; enable the Agent plugin for this profile."
        )
    return bridges[0].get_plugin_context()


def _available_llm_options() -> List[Dict[str, Any]]:
    """Return the active profile's configured provider/model choices from Hermes' picker catalog."""
    from hermes_cli.inventory import build_models_payload, load_picker_context

    payload = build_models_payload(
        load_picker_context(), explicit_only=True, for_picker=True,
        non_blocking_catalogs=True, probe_custom_providers=False,
        probe_current_custom_provider=False,
    )
    return [
        {
            "id": str(row.get("slug") or ""),
            "label": str(row.get("name") or row.get("slug") or ""),
            "models": [str(model) for model in row.get("models", []) if isinstance(model, str)],
        }
        for row in payload.get("providers", [])
        if row.get("slug") and row.get("models")
    ]


def _llm_settings() -> Dict[str, str]:
    ctx = _plugin_context()
    routing = ctx.get_config("compaction_routing", default={})
    if not isinstance(routing, dict):
        routing = {}
    return {
        "provider": str(
            routing.get("provider") or ctx.get_config("compaction_provider", default="") or ""
        ),
        "model": str(
            routing.get("model") or ctx.get_config("compaction_model", default="") or ""
        ),
    }


def _memory_target(store: Any, target: str) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")
    entries = store.memory_entries if target == "memory" else store.user_entries
    used_chars = int(store._char_count(target))
    char_limit = int(store._char_limit(target))
    return {
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
            return {"success": True, "output": "No pending memory writes."}
    else:
        record, error = review._resolve(selector)
        if error:
            raise HTTPException(status_code=404, detail=error)
        assert record is not None
        records = [record]

    if action == "reject":
        removed = 0
        failed = []
        for record in records:
            try:
                record.path.unlink()
                removed += 1
            except Exception as exc:
                failed.append(f"{record.id}: {exc}")

        if selector == "all":
            output = f"Rejected {removed} pending memory write(s)."
        elif removed == 1:
            output = f"Rejected pending memory write '{records[0].id}'."
        else:
            output = "Rejected 0 pending memory write(s)."
        if failed:
            output += "\nFailed:\n" + "\n".join(f"  {item}" for item in failed)
        return {"success": True, "output": output}

    store = _memory_store()
    applied = 0
    failed = []
    overwritten = []
    removed = []
    for record in records:
        try:
            result = _apply_pending_memory(record.payload, store)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if result.get("success"):
            applied += 1
            overwritten.extend(f"  {record.id}: {text}" for text in _changed_entries(result, "replaced"))
            removed.extend(f"  {record.id}: {text}" for text in _changed_entries(result, "removed"))
            try:
                record.path.unlink()
            except Exception as exc:
                failed.append(f"{record.id}: applied but could not remove pending record: {exc}")
        else:
            failed.append(f"{record.id}: {result.get('error') or 'approval failed'}")

    output = f"Approved {applied} memory write(s)."
    if overwritten:
        output += "\nOverwrote entire entry (re-add anything you still need):\n" + "\n".join(overwritten)
    if removed:
        output += "\nRemoved entry (re-add anything you still need):\n" + "\n".join(removed)
    if failed:
        output += "\nFailed:\n" + "\n".join(f"  {item}" for item in failed)
    return {"success": True, "output": output}


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
    settings = _llm_settings()
    model = settings["model"] or None
    provider = settings["provider"] or None
    return build_memory_compaction_preview(
        llm, target, entries, _entry_delimiter(), model=model, provider=provider,
    )


@router.get("/llm/options")
def llm_options() -> Dict[str, Any]:
    providers = _available_llm_options()
    settings = _llm_settings()
    provider = settings["provider"]
    selected = next((row for row in providers if row["id"] == provider), None)
    model = settings["model"]
    if not selected or model not in selected["models"]:
        provider, model = "", ""
    return {"providers": providers, "provider": provider, "model": model}


@router.put("/llm/settings")
def save_llm_settings(body: LlmSettingsRequest) -> Dict[str, Any]:
    provider, model = body.provider.strip(), body.model.strip()
    if bool(provider) != bool(model):
        raise HTTPException(status_code=400, detail="Select both a provider and model, or clear both to use Hermes defaults.")
    if provider and not any(
        row["id"] == provider and model in row["models"] for row in _available_llm_options()
    ):
        raise HTTPException(status_code=400, detail="Choose a provider and model from the active profile's available list.")
    try:
        ctx = _plugin_context()
        ctx.set_config("compaction_routing", {"provider": provider, "model": model})
    except Exception:
        raise HTTPException(status_code=503, detail="Could not save Magi LLM settings.") from None
    return {"success": True, "provider": provider, "model": model}


@router.post("/memory/{target}/compact")
def apply_memory_compaction(target: str, body: MemoryCompactionApplyRequest) -> Dict[str, Any]:
    if target not in {"memory", "user"}:
        raise HTTPException(status_code=400, detail="target must be 'memory' or 'user'.")

    proposed = [entry.strip() for entry in body.entries]
    if not proposed or any(not entry for entry in proposed):
        raise HTTPException(status_code=400, detail="entries must contain at least one non-empty memory entry.")
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
    result = _apply_compaction_batch(store, target, operations, current)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)

    return {
        "success": True,
        "result": result,
        "target": _memory_target(store, target),
    }
