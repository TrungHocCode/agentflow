"""Standalone Tool Lab runner.

This runner intentionally bypasses Supervisor/Worker/LLM execution.  It makes
tool behavior observable and testable in isolation before a tool is trusted by
an end-to-end workflow.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.execution.tools.contracts import ToolResult, parse_tool_result
from app.execution.tools.registry import autodiscover_tools
from app.execution.tools.base import ToolRegistry


def run_tool(tool_name: str, arguments: dict[str, Any]) -> ToolResult:
    """Invoke one registered tool and normalize its result."""

    autodiscover_tools()
    started = perf_counter()
    try:
        tool = ToolRegistry.get_tool(tool_name)
    except ValueError as exc:
        return parse_tool_result(
            {
                "ok": False,
                "status": "invalid_input",
                "error": {
                    "code": "unknown_tool",
                    "message": str(exc),
                    "retryable": False,
                },
                "metadata": {"tool_name": tool_name},
            },
            tool_name=tool_name,
        )

    try:
        raw_result = tool.invoke(arguments)
        result = parse_tool_result(raw_result, tool_name=tool_name)
    except Exception as exc:  # pragma: no cover - defensive runtime boundary
        result = parse_tool_result(
            {
                "ok": False,
                "status": "internal_error",
                "error": {
                    "code": "tool_invocation_failed",
                    "message": str(exc),
                    "retryable": False,
                },
                "metadata": {"tool_name": tool_name},
            },
            tool_name=tool_name,
        )

    result.metadata.tool_name = result.metadata.tool_name or tool_name
    result.metadata.duration_ms = round((perf_counter() - started) * 1000)
    return result
