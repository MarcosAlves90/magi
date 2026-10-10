"""Runtime bridge between the Agent and dashboard halves of the plugin.

Hermes imports ``__init__.py`` and ``dashboard/plugin_api.py`` as separate
modules inside the same plugin-host process.  The Agent half receives the
supported ``PluginContext``; dashboard routes do not.  This module keeps only
the already-bound context so backend requests can reuse ``ctx.llm`` without
constructing a provider client or depending on a chat session.
"""

from __future__ import annotations

from typing import Any


_plugin_context: Any = None


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
    return _plugin_context.llm
