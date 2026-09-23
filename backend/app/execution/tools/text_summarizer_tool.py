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
    text: str = Field(description="Article text or a structured tool result containing research evidence.")
    max_bullet_points: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum representative evidence sentences to extract; this tool does not paraphrase.",
    )


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
    articles = data.get("articles")
    if isinstance(articles, list):
        article_evidence = [
            str(article.get("text") or "").strip()
            for article in articles
            if isinstance(article, dict)
            and article.get("status") in {"success", "partial"}
            and str(article.get("text") or "").strip()
        ]
        return "\n\n".join(article_evidence), result

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


def _source_urls(source_result: ToolResult | None) -> list[str]:
    """Preserve URLs from structured source outputs in the extracted digest."""

    if source_result is None:
        return []
    urls: list[str] = []
    data = source_result.data if isinstance(source_result.data, dict) else {}
    has_article_records = isinstance(data.get("articles"), list)
    articles = data.get("articles")
    if isinstance(articles, list):
        for article in articles:
            if not isinstance(article, dict) or article.get("status") not in {"success", "partial"}:
                continue
            url = article.get("final_url") or article.get("requested_url")
            if isinstance(url, str) and url not in urls:
                urls.append(url)
    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            url = item.get("url") if isinstance(item, dict) else None
            if isinstance(url, str) and url not in urls:
                urls.append(url)
    if (
        not urls
        and not has_article_records
        and source_result.source
        and source_result.source.final_url
    ):
        urls.append(source_result.source.final_url)
    return urls


@ToolRegistry.register_tool(name="text_summarizer")
@tool("text_summarizer", args_schema=TextSummarizerInput)
def text_summarizer(text: str, max_bullet_points: int = 5) -> str:
    """Extract representative evidence sentences without semantic paraphrasing."""

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
        "method": "extractive_sentence_sampling",
    }
    source_urls = _source_urls(source_result)
    metadata["source_count"] = len(source_urls)
    if source_result is not None:
        metadata["source_status"] = source_result.status

    return success_result(
        {
            "summary": "\n".join(selected_bullets),
            "bullets": selected_bullets,
            "formatted": formatted,
            "sources": source_urls,
        },
        tool_name="text_summarizer",
        source=source_result.source if source_result else None,
        metadata=metadata,
    ).to_json()
