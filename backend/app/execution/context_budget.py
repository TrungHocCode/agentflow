"""Bound worker observations and guard the complete request, including tool schemas."""

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core.config import settings
from app.execution.tools.contracts import ToolResult


class ContextBudgetExceeded(ValueError):
    """A request cannot fit without losing required inputs."""


def estimate_tokens(value: str) -> int:
    """Conservative UTF-8 heuristic; not a matching tokenizer or exact upper bound."""
    return (len(value.encode("utf-8")) + 1) // 2


def request_estimate(messages: list[BaseMessage], tools: list[BaseTool]) -> int:
    payload = {
        "messages": [
            {"role": message.type, "content": message.content,
             "tool_calls": getattr(message, "tool_calls", [])}
            for message in messages
        ],
        "tools": [convert_to_openai_tool(tool) for tool in tools],
    }
    return estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str)) + 64 * len(messages)


def guard_context(messages: list[BaseMessage], tools: list[BaseTool]) -> int:
    """Compact complete old tool exchanges only; never remove pending tool-call pairs."""
    budget = settings.LLM_CONTEXT_TOKENS - settings.LLM_OUTPUT_TOKENS - settings.LLM_CONTEXT_MARGIN_TOKENS
    if budget < 256:
        raise ContextBudgetExceeded("Invalid context configuration: no usable input budget.")
    estimate = request_estimate(messages, tools)
    while estimate > budget:
        # Keep the current observation exchange and every system/user constraint.
        assistant_indexes = [i for i, message in enumerate(messages) if isinstance(message, AIMessage)
                             and message.tool_calls]
        if len(assistant_indexes) < 2:
            break
        start, end = assistant_indexes[0], assistant_indexes[1]
        exchange = messages[start + 1:end]
        expected = {call["id"] for call in messages[start].tool_calls}
        observed = {message.tool_call_id for message in exchange if isinstance(message, ToolMessage)}
        if not exchange or any(not isinstance(message, ToolMessage) for message in exchange) or expected != observed:
            break
        del messages[start:end]
        estimate = request_estimate(messages, tools)
    if estimate > budget:
        raise ContextBudgetExceeded(
            f"Estimated input {estimate} tokens exceeds input budget {budget}; "
            "reduce evidence or increase the configured context. Counting method: utf8/2 heuristic."
        )
    return estimate


def project_tool_result(result: ToolResult, max_chars: int = 5000) -> str:
    """Return bounded JSON without cutting serialized JSON or duplicating search candidates."""
    payload: dict[str, Any] = {"ok": result.ok, "status": result.status}
    if result.error:
        payload["error"] = result.error.model_dump()
    if result.source:
        payload["source"] = result.source.model_dump(exclude_none=True)
    data = result.data
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        candidates = []
        seen = set()
        for record in data["results"]:
            if not isinstance(record, dict) or record.get("url") in seen:
                continue
            seen.add(record.get("url"))
            candidates.append({key: str(record[key])[:400] for key in ("title", "url", "snippet", "body")
                               if key in record})
        payload["data"] = {"candidates": candidates[:8], "total_candidates": len(candidates),
                           "discovery_only": True}
    else:
        payload["data"] = data
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return serialized
    # Explicit preview, never pretend a truncated tool body is complete evidence.
    return json.dumps({"ok": result.ok, "status": result.status, "projection_partial": True,
                       "original_chars": len(serialized), "preview": serialized[:max_chars // 3],
                       "warning": "Only a bounded preview is shown; do not infer complete source coverage."},
                      ensure_ascii=False)
