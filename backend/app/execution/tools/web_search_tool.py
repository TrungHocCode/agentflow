"""Structured DuckDuckGo HTML search tool."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import SourceMetadata, failure_result, success_result


SEARCH_URL = "https://html.duckduckgo.com/html/"
SEARCH_TIMEOUT = (5, 12)
MAX_SEARCH_RESPONSE_BYTES = 2 * 1024 * 1024


class WebSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="The query string to search on the web.")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum number of search results.")


class DuckDuckGoResultParser(HTMLParser):
    """Parse result cards without relying on brittle regular expressions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._result_depth = 0
        self._current: dict[str, str] | None = None
        self._active_field: str | None = None
        self._active_text: list[str] = []
        self._active_href: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())

        if tag == "div" and ("result" in classes or "result__body" in classes) and self._current is None:
            self._current = {}
            self._result_depth = 1
            return
        if self._current is None:
            return
        if tag == "div" and self._result_depth:
            self._result_depth += 1

        if tag == "a":
            if "result__a" in classes:
                self._active_field = "title"
            elif "result__url" in classes:
                self._active_field = "url"
            elif "result__snippet" in classes:
                self._active_field = "snippet"
            else:
                self._active_field = None
            self._active_text = []
            self._active_href = attrs_map.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag == "a" and self._active_field:
            value = self._active_href if self._active_field == "url" and self._active_href else self._clean_text(" ".join(self._active_text))
            if value:
                self._current[self._active_field] = value
            self._active_field = None
            self._active_text = []
            self._active_href = ""
        elif tag == "div" and self._result_depth:
            self._result_depth -= 1
            if self._result_depth == 0:
                if not self._current.get("title") and self._current.get("snippet"):
                    self._current["title"] = self._current["snippet"][:200]
                if self._current.get("title") and self._current.get("url"):
                    self.results.append(self._current)
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._active_field:
            self._active_text.append(data)

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(unescape(value).split())


def _resolve_result_url(raw_url: str) -> str:
    """Resolve DuckDuckGo redirect links to their original destination."""

    candidate = urljoin(SEARCH_URL, raw_url)
    parsed = urlparse(candidate)
    target = parse_qs(parsed.query).get("uddg", [None])[0]
    return target or candidate


def _parse_results(html: str, max_results: int) -> list[dict[str, str | int]]:
    parser = DuckDuckGoResultParser()
    parser.feed(html)
    normalized: list[dict[str, str | int]] = []
    seen_urls: set[str] = set()
    for result in parser.results:
        url = _resolve_result_url(result["url"])
        if url in seen_urls or not url.startswith(("http://", "https://")):
            continue
        seen_urls.add(url)
        normalized.append(
            {
                "rank": len(normalized) + 1,
                "title": result["title"],
                "url": url,
                "snippet": result.get("snippet", ""),
            }
        )
        if len(normalized) >= max_results:
            break
    return normalized


@ToolRegistry.register_tool(name="web_search")
@tool("web_search", args_schema=WebSearchInput)
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return ranked, cited result records as JSON."""

    started = perf_counter()
    normalized_query = " ".join(str(query or "").split())
    if len(normalized_query) < 2:
        return failure_result(
            "invalid_input",
            code="query_too_short",
            message="Search query must contain at least two non-whitespace characters.",
            tool_name="web_search",
        ).to_json()

    headers = {
        "User-Agent": "Mozilla/5.0 AgentFlowWebSearch/2.0",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        response = requests.post(
            SEARCH_URL,
            headers=headers,
            data={"q": normalized_query},
            timeout=SEARCH_TIMEOUT,
        )
    except requests.Timeout:
        return failure_result(
            "timeout",
            code="search_timeout",
            message="Timed out while fetching search results.",
            retryable=True,
            tool_name="web_search",
            source=SourceMetadata(requested_url=SEARCH_URL),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()
    except requests.RequestException as exc:
        return failure_result(
            "http_error",
            code="search_request_failed",
            message=str(exc),
            retryable=True,
            tool_name="web_search",
            source=SourceMetadata(requested_url=SEARCH_URL),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    status_code = getattr(response, "status_code", None)
    source = SourceMetadata(
        requested_url=SEARCH_URL,
        final_url=SEARCH_URL,
        status_code=status_code if isinstance(status_code, int) else None,
        content_type="text/html",
    )
    if not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
        retryable = status_code in {408, 425, 429} or (isinstance(status_code, int) and status_code >= 500)
        return failure_result(
            "http_error",
            code="unexpected_search_status",
            message=f"Search provider returned HTTP status {status_code}.",
            retryable=retryable,
            tool_name="web_search",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    raw_content = getattr(response, "content", None)
    if isinstance(raw_content, bytes) and len(raw_content) > MAX_SEARCH_RESPONSE_BYTES:
        return failure_result(
            "blocked",
            code="search_response_too_large",
            message=f"Search response exceeds the {MAX_SEARCH_RESPONSE_BYTES} byte limit.",
            tool_name="web_search",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    try:
        results = _parse_results(getattr(response, "text", ""), max_results)
    except Exception as exc:
        return failure_result(
            "parse_error",
            code="search_parse_failed",
            message=str(exc),
            tool_name="web_search",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    metadata = {
        "duration_ms": round((perf_counter() - started) * 1000),
        "result_count": len(results),
        "parser": "agentflow_duckduckgo_html_v2",
    }
    if not results:
        return failure_result(
            "empty",
            code="no_search_results",
            message="The search provider returned no parseable results.",
            tool_name="web_search",
            source=source,
            metadata=metadata,
        ).model_copy(update={"data": {"query": normalized_query, "results": []}}).to_json()

    return success_result(
        {"query": normalized_query, "results": results},
        tool_name="web_search",
        source=source,
        metadata=metadata,
    ).to_json()
