"""Run a small set of complementary web searches with bounded concurrency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import ToolResult, failure_result, parse_tool_result, success_result


MAX_QUERIES = 5
MAX_CONCURRENCY = 3


class WebSearchBatchInput(BaseModel):
    """Arguments for searching distinct angles of one research question."""

    queries: list[str] = Field(
        min_length=1,
        max_length=MAX_QUERIES,
        description="One to five distinct, focused web-search queries for the same research question.",
    )
    max_results_per_query: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of ranked results to request for each query.",
    )
    concurrency: int = Field(
        default=3,
        ge=1,
        le=MAX_CONCURRENCY,
        description="Maximum number of searches to run simultaneously; hard-capped at three.",
    )


def _canonical_url(raw_url: str) -> str:
    """Remove fragments for deduplication without collapsing meaningful query parameters."""

    without_fragment, _ = urldefrag(raw_url.strip())
    try:
        parts = urlsplit(without_fragment)
    except ValueError:
        return without_fragment.casefold()
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def _search_one(query: str, max_results: int) -> tuple[str, ToolResult]:
    try:
        raw_result = ToolRegistry.get_tool("web_search").invoke(
            {"query": query, "max_results": max_results}
        )
        return query, parse_tool_result(raw_result, tool_name="web_search")
    except Exception as exc:  # Keep one provider failure from discarding other searches.
        return query, failure_result(
            "internal_error",
            code="batch_search_failed",
            message=str(exc),
            retryable=True,
            tool_name="web_search",
        )


@ToolRegistry.register_tool(name="web_search_batch")
@tool("web_search_batch", args_schema=WebSearchBatchInput)
def web_search_batch(
    queries: list[str],
    max_results_per_query: int = 5,
    concurrency: int = MAX_CONCURRENCY,
) -> str:
    """Search several complementary queries concurrently and return deduplicated citations."""

    started = perf_counter()
    normalized_queries: list[str] = []
    for query in queries:
        normalized = " ".join(str(query or "").split())
        if len(normalized) >= 2 and normalized.casefold() not in {
            item.casefold() for item in normalized_queries
        }:
            normalized_queries.append(normalized)

    if not normalized_queries:
        return failure_result(
            "invalid_input",
            code="no_valid_queries",
            message="Provide at least one distinct query with two or more characters.",
            tool_name="web_search_batch",
        ).to_json()

    worker_count = min(concurrency, MAX_CONCURRENCY, len(normalized_queries))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agentflow-search") as pool:
        query_results = list(
            pool.map(
                lambda query: _search_one(query, max_results_per_query),
                normalized_queries,
            )
        )

    query_records: list[dict[str, Any]] = []
    merged_results: list[dict[str, Any]] = []
    result_indexes: dict[str, int] = {}
    succeeded = 0

    for query, result in query_results:
        data = result.data if isinstance(result.data, dict) else {}
        candidates = data.get("results") if isinstance(data.get("results"), list) else []
        valid_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("url"):
                continue
            record = dict(candidate)
            record["query"] = query
            valid_candidates.append(record)
            key = _canonical_url(str(record["url"]))
            existing_index = result_indexes.get(key)
            if existing_index is None:
                record["matched_queries"] = [query]
                result_indexes[key] = len(merged_results)
                merged_results.append(record)
            else:
                matches = merged_results[existing_index].setdefault("matched_queries", [])
                if query not in matches:
                    matches.append(query)

        if result.ok and valid_candidates:
            succeeded += 1
        query_record: dict[str, Any] = {
            "query": query,
            "status": result.status,
            "result_count": len(valid_candidates),
            "results": valid_candidates,
            "metadata": result.metadata.model_dump(exclude_none=True),
        }
        if result.source:
            query_record["source"] = result.source.model_dump(exclude_none=True)
        if result.error:
            query_record["error"] = result.error.model_dump()
        query_records.append(query_record)

    metadata = {
        "duration_ms": round((perf_counter() - started) * 1000),
        "query_count": len(normalized_queries),
        "successful_query_count": succeeded,
        "result_count": len(merged_results),
        "max_concurrency": worker_count,
    }
    data = {
        "queries": query_records,
        "results": merged_results,
        "deduplicated_count": sum(
            len(record["results"]) for record in query_records
        ) - len(merged_results),
    }

    if succeeded == 0:
        return failure_result(
            "empty",
            code="all_queries_failed",
            message="No query returned usable search results.",
            retryable=True,
            tool_name="web_search_batch",
            metadata=metadata,
        ).model_copy(update={"data": data}).to_json()

    status = "success" if succeeded == len(normalized_queries) else "partial"
    return success_result(
        data,
        tool_name="web_search_batch",
        metadata=metadata,
        status=status,
    ).to_json()
