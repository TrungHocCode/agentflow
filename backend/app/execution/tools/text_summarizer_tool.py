"""Deterministic evidence-aware text summarizer."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import ToolResult, failure_result, success_result


class TextSummarizerInput(BaseModel):
    text: str = Field(description="Article text or a structured tool result to summarize.")
    max_bullet_points: int = Field(default=5, ge=1, le=20, description="Maximum key bullet points.")


def _structured_input(value: str) -> tuple[str, ToolResult | None]:
    """Extract evidence text from crawler/search JSON while preserving status."""

    try:
        payload: Any = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value.strip(), None

    if not isinstance(payload, dict) or not {"ok", "status"}.issubset(payload):
        return value.strip(), None

    result = ToolResult.model_validate(payload)
    if not result.ok or result.status in {"empty", "http_error", "timeout", "blocked", "parse_error", "internal_error"}:
        return "", result

    data = result.data if isinstance(result.data, dict) else {"value": result.data}
    text = str(data.get("text") or "").strip()
    if not text and isinstance(data.get("results"), list):
        text = "\n".join(
            f"{item.get('title', '')}: {item.get('snippet', '')}"
            for item in data["results"]
            if isinstance(item, dict)
        ).strip()
    if not text and isinstance(data.get("items"), list):
        text = "\n".join(
            f"{item.get('title', '')}: {item.get('url', '')}"
            for item in data["items"]
            if isinstance(item, dict)
        ).strip()
    return text, result


@ToolRegistry.register_tool(name="text_summarizer")
@tool("text_summarizer", args_schema=TextSummarizerInput)
def text_summarizer(text: str, max_bullet_points: int = 5) -> str:
    """Create deterministic bullets only from supplied evidence."""

    source_text, source_result = _structured_input(text)
    if source_result is not None and not source_result.ok:
        error = source_result.error
        return failure_result(
            source_result.status,
            code="insufficient_evidence",
            message=error.message if error else "The input tool did not provide usable evidence.",
            retryable=False,
            tool_name="text_summarizer",
            source=source_result.source,
            metadata={"source_status": source_result.status},
        ).to_json()

    if not source_text:
        return failure_result(
            "empty",
            code="empty_summary_input",
            message="Provided evidence is empty.",
            tool_name="text_summarizer",
        ).to_json()

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", source_text)
        if len(sentence.strip()) >= 16
    ]
    if not sentences:
        sentences = [source_text[:500].strip()]

    selected_bullets: list[str] = []
    step = max(1, len(sentences) // max_bullet_points)
    for index in range(0, len(sentences), step):
        if len(selected_bullets) >= max_bullet_points:
            break
        selected_bullets.append(sentences[index])

    formatted = (
        f"=== TEXT SUMMARY ({len(selected_bullets)} Bullet Points) ===\n"
        + "\n".join(f"- {bullet}" for bullet in selected_bullets)
        + "\n==========================================================="
    )
    metadata = {
        "input_characters": len(source_text),
        "bullet_count": len(selected_bullets),
        "input_sha256": sha256(source_text.encode("utf-8")).hexdigest(),
    }
    if source_result is not None:
        metadata["source_status"] = source_result.status

    return success_result(
        {
            "summary": "\n".join(selected_bullets),
            "bullets": selected_bullets,
            "formatted": formatted,
        },
        tool_name="text_summarizer",
        source=source_result.source if source_result else None,
        metadata=metadata,
    ).to_json()
