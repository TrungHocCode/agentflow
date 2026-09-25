"""Crawl several selected web sources concurrently with per-source outcomes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urldefrag

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import ToolResult, failure_result, parse_tool_result, success_result
from app.execution.tools.news_crawler_tool import normalize_news_url
from app.execution.tools.network_policy import validate_external_url


MAX_URLS = 8
MAX_CONCURRENCY = 3
MAX_SOURCE_TEXT_CHARS = 8_000


class NewsCrawlerBatchInput(BaseModel):
    """Bounded batch crawl arguments."""

    urls: list[str] = Field(
        min_length=1,
        max_length=MAX_URLS,
        description="One to eight specific article or listing URLs selected from research results.",
    )
    mode: Literal["auto", "static", "browser"] = Field(
        default="auto",
        description="Use static extraction first, with browser fallback when needed, or force one mode.",
    )
    max_articles_per_listing: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Optional one-level listing expansion per URL; keep at 0 for already selected article URLs.",
    )
    article_mode: Literal["auto", "static", "browser"] = Field(
        default="static",
        description="Rendering mode used only when expanding links from a listing page.",
    )
    concurrency: int = Field(
        default=3,
        ge=1,
        le=MAX_CONCURRENCY,
        description="Maximum number of source URLs to crawl simultaneously; hard-capped at three.",
    )


def _crawl_one(
    url: str,
    *,
    mode: Literal["auto", "static", "browser"],
    max_articles: int,
    article_mode: Literal["auto", "static", "browser"],
) -> tuple[str, ToolResult]:
    try:
        safe_url, network_error = validate_external_url(url)
        if network_error or not safe_url:
            return url, failure_result(
                "blocked",
                code="outbound_url_blocked",
                message=network_error or "URL is not allowed.",
                tool_name="news_crawler",
            )
        raw_result = ToolRegistry.get_tool("news_crawler").invoke(
            {
                "url": safe_url,
                "mode": mode,
                "max_articles": max_articles,
                "article_mode": article_mode,
            }
        )
        return url, parse_tool_result(raw_result, tool_name="news_crawler")
    except Exception as exc:  # Keep a failing page isolated from the rest of the batch.
        return url, failure_result(
            "internal_error",
            code="batch_crawl_failed",
            message=str(exc),
            retryable=True,
            tool_name="news_crawler",
        )


def _source_record(url: str, result: ToolResult) -> dict[str, Any]:
    data = dict(result.data) if isinstance(result.data, dict) else {}
    original_text_length = 0
    text = data.get("text")
    if isinstance(text, str):
        original_text_length = len(text)
        if original_text_length > MAX_SOURCE_TEXT_CHARS:
            data["text"] = text[:MAX_SOURCE_TEXT_CHARS]
            data["text_truncated"] = True
            data["original_text_length"] = original_text_length

    # The crawler provides equivalent text, Markdown, and paragraph representations.
    # Keep one bounded body in a multi-source response to control local-model context use.
    data.pop("markdown", None)
    data.pop("paragraphs", None)
    articles = data.get("articles")
    if isinstance(articles, list):
        bounded_articles: list[Any] = []
        for article in articles:
            if not isinstance(article, dict):
                bounded_articles.append(article)
                continue
            bounded_article = dict(article)
            article_text = bounded_article.get("text")
            if isinstance(article_text, str) and len(article_text) > MAX_SOURCE_TEXT_CHARS:
                bounded_article["text"] = article_text[:MAX_SOURCE_TEXT_CHARS]
                bounded_article["text_truncated"] = True
                bounded_article["original_text_length"] = len(article_text)
            bounded_articles.append(bounded_article)
        data["articles"] = bounded_articles
    items = data.get("items")
    if isinstance(items, list) and len(items) > 20:
        data["items"] = items[:20]
        data["items_truncated"] = True
        data["original_item_count"] = len(items)

    return {
        "requested_url": url,
        "ok": result.ok,
        "status": result.status,
        "data": data,
        **({"source": result.source.model_dump(exclude_none=True)} if result.source else {}),
        "metadata": result.metadata.model_dump(exclude_none=True),
        **({"error": result.error.model_dump()} if result.error else {}),
    }


@ToolRegistry.register_tool(name="news_crawler_batch")
@tool("news_crawler_batch", args_schema=NewsCrawlerBatchInput)
def news_crawler_batch(
    urls: list[str],
    mode: Literal["auto", "static", "browser"] = "auto",
    max_articles_per_listing: int = 0,
    article_mode: Literal["auto", "static", "browser"] = "static",
    concurrency: int = MAX_CONCURRENCY,
) -> str:
    """Crawl selected URLs concurrently and return full extracted content per source."""

    started = perf_counter()
    normalized_urls: list[str] = []
    rejected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for raw_url in urls:
        normalized, syntax_error = normalize_news_url(raw_url)
        if syntax_error or not normalized:
            rejected.append({
                "requested_url": raw_url,
                "ok": False,
                "status": "invalid_input",
                "error": {"code": "invalid_url", "message": syntax_error or "URL is invalid."},
            })
            continue
        dedupe_key, _ = urldefrag(normalized)
        if dedupe_key not in seen_urls:
            seen_urls.add(dedupe_key)
            normalized_urls.append(normalized)

    worker_count = min(concurrency, MAX_CONCURRENCY, len(normalized_urls))
    if normalized_urls:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agentflow-crawl") as pool:
            outcomes = list(
                pool.map(
                    lambda url: _crawl_one(
                        url,
                        mode=mode,
                        max_articles=max_articles_per_listing,
                        article_mode=article_mode,
                    ),
                    normalized_urls,
                )
            )
    else:
        outcomes = []

    source_records = [_source_record(url, result) for url, result in outcomes]
    source_records.extend(rejected)
    success_count = sum(1 for record in source_records if record.get("ok"))
    partial_count = sum(1 for record in source_records if record.get("status") == "partial")
    metadata = {
        "duration_ms": round((perf_counter() - started) * 1000),
        "requested_url_count": len(urls),
        "unique_url_count": len(normalized_urls),
        "successful_source_count": success_count,
        "failed_source_count": len(source_records) - success_count,
        "partial_source_count": partial_count,
        "max_concurrency": worker_count,
    }
    data = {"sources": source_records}

    if success_count == 0:
        source_statuses = {str(item.get("status")) for item in source_records}
        status_by_all_sources = {
            "invalid_input": "invalid_input",
            "blocked": "blocked",
            "timeout": "timeout",
            "http_error": "http_error",
            "parse_error": "parse_error",
        }
        status = (
            status_by_all_sources.get(next(iter(source_statuses)), "empty")
            if len(source_statuses) == 1
            else "empty"
        )
        return failure_result(
            status,
            code="no_sources_crawled",
            message="No URL produced a usable crawl result; inspect per-source statuses and errors.",
            retryable=status in {"timeout", "http_error"},
            tool_name="news_crawler_batch",
            metadata=metadata,
        ).model_copy(update={"data": data}).to_json()

    result_status = (
        "success"
        if success_count == len(source_records) and partial_count == 0
        else "partial"
    )
    return success_result(
        data,
        tool_name="news_crawler_batch",
        metadata=metadata,
        status=result_status,
    ).to_json()
