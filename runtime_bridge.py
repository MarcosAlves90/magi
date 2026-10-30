"""Runtime bridge between the Agent and dashboard halves of the plugin.

Hermes imports ``__init__.py`` and ``dashboard/plugin_api.py`` as separate
modules inside the same plugin-host process.  The Agent half receives the
supported ``PluginContext``; dashboard routes do not.  This module keeps only
the already-bound context so backend requests can reuse ``ctx.llm`` without
constructing a provider client or depending on a chat session.
"""

from __future__ import annotations

from contextvars import copy_context
from typing import Any


_plugin_context: Any = None


class _CapabilityAwareLlm:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._context = copy_context()

    def complete_structured(self, **kwargs: Any) -> Any:
        def invoke() -> Any:
            if kwargs.get("model"):
                try:
                    granted = self._ctx.has_capability("llm.model_override")
                except Exception:
                    granted = False
                if granted is not True:
                    raise PermissionError(
                        "Magi model overrides require the llm.model_override capability."
                    )
            return self._ctx.llm.complete_structured(**kwargs)

        # Hermes resolves the active profile from ContextVars. The IPC server
        # handles requests on worker threads, so restore the registration context
        # with a fresh copy for each call rather than sharing a non-reentrant one.
        return self._context.copy().run(invoke)


def bind_plugin_context(ctx: Any) -> None:
    global _plugin_context
    _plugin_context = ctx


def clear_plugin_context(ctx: Any = None) -> None:
    global _plugin_context
    if ctx is None or _plugin_context is ctx:
        _plugin_context = None


def get_plugin_llm() -> Any:
    if _plugin_context is None:
        raise RuntimeError(
            "Magi Agent context is unavailable; enable the Agent plugin for this profile."
        )
    return _CapabilityAwareLlm(_plugin_context)
