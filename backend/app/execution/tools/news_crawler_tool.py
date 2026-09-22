"""HTTP HTML crawler with page classification and quality-aware output."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlparse
from collections.abc import Mapping

import requests
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.cache import get_cached, set_cached
from app.execution.tools.contracts import (
    SourceMetadata,
    failure_result,
    success_result,
)
from app.execution.tools.network_policy import MAX_TRANSIENT_ATTEMPTS, transient_backoff, validate_external_url


MIN_ARTICLE_TEXT_LENGTH = 80
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = (5, 15)
MAX_REDIRECTS = 3
SKIPPED_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "template", "svg"}
TEXT_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote"}


class NewsCrawlerInput(BaseModel):
    url: str = Field(description="The news article or web page URL to crawl.")


class HTMLContentExtractor(HTMLParser):
    """Small dependency-free DOM-aware extractor for static HTML pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.blocks: list[str] = []
        self.links: list[dict[str, str]] = []
        self.canonical_url: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._current_block_tag: str | None = None
        self._current_text: list[str] = []
        self._current_link: dict[str, str] | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}

        if tag in SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs_map.get("name") or attrs_map.get("property") or "").lower()
            content = attrs_map.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        elif tag == "link" and attrs_map.get("rel", "").lower() == "canonical":
            self.canonical_url = attrs_map.get("href") or None
        elif tag == "a":
            self._current_link = {"href": attrs_map.get("href", "")}
            self._current_link_text = []

        if tag in TEXT_BLOCK_TAGS and self._current_block_tag is None:
            self._current_block_tag = tag
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIPPED_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return

        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_link is not None:
            text = self._normalize_text(" ".join(self._current_link_text))
            href = self._normalize_text(self._current_link.get("href", ""))
            if href:
                self.links.append({"href": href, "text": text})
            self._current_link = None
            self._current_link_text = []
        elif tag == self._current_block_tag:
            text = self._normalize_text(" ".join(self._current_text))
            if text:
                self.blocks.append(text)
            self._current_block_tag = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._current_block_tag:
            self._current_text.append(data)
        if self._current_link is not None:
            self._current_link_text.append(data)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(unescape(value).split())


def normalize_news_url(raw_url: str) -> tuple[str | None, str | None]:
    """Normalize common LLM URL formatting and validate HTTP(S) URLs."""

    candidate = str(raw_url or "").strip()
    match = re.search(r"https?://[^\s<>\[\]\\\"']+", candidate)
    if match:
        candidate = match.group(0)
    candidate = candidate.strip("<>").rstrip(".,;:!?)]}")

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "URL must be an absolute HTTP(S) URL."
    return candidate, None


def _is_hacker_news_listing(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower().endswith("news.ycombinator.com") and parsed.path in {"", "/"}


def _build_listing_items(parser: HTMLContentExtractor, base_url: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        text = link["text"]
        href = urljoin(base_url, link["href"])
        parsed = urlparse(href)
        if len(text) < 5 or parsed.scheme not in {"http", "https"} or href in seen:
            continue
        seen.add(href)
        items.append({"title": text, "url": href})
    return items[:50]


def _extract_page(url: str, html: str) -> tuple[str, dict[str, Any], list[str]]:
    parser = HTMLContentExtractor()
    parser.feed(html)

    title = " ".join(parser.title_parts).strip() or parser.meta.get("og:title", "").strip()
    paragraphs = parser.blocks[:50]
    body = "\n\n".join(paragraphs)
    links = _build_listing_items(parser, url)
    warnings: list[str] = []

    is_listing = (_is_hacker_news_listing(url) and bool(links)) or (
        len(links) >= 10 and len(body) < 6000 and len(paragraphs) < 8
    )
    if is_listing:
        content_type = "listing"
        if not links:
            warnings.append("Listing page did not contain extractable linked items.")
        data = {
            "content_type": content_type,
            "title": title or "Untitled listing",
            "text": body,
            "paragraphs": paragraphs,
            "items": links,
            "metadata": {
                "author": parser.meta.get("author"),
                "published_at": parser.meta.get("article:published_time")
                or parser.meta.get("datepublished"),
                "canonical_url": parser.canonical_url,
            },
        }
        if not links:
            warnings.append("No listing items were found.")
        return ("success" if links else "empty", data, warnings)

    if len(body) < MIN_ARTICLE_TEXT_LENGTH:
        warnings.append(
            f"Extracted article text is below the minimum quality threshold ({MIN_ARTICLE_TEXT_LENGTH} characters)."
        )
        return (
            "empty",
            {
                "content_type": "empty",
                "title": title or "Untitled page",
                "text": body,
                "paragraphs": paragraphs,
                "items": [],
                "metadata": {"canonical_url": parser.canonical_url},
            },
            warnings,
        )

    return (
        "success",
        {
            "content_type": "article",
            "title": title or "Untitled article",
            "text": body[:30000],
            "paragraphs": paragraphs[:20],
            "items": [],
            "metadata": {
                "author": parser.meta.get("author"),
                "published_at": parser.meta.get("article:published_time")
                or parser.meta.get("datepublished"),
                "description": parser.meta.get("description") or parser.meta.get("og:description"),
                "canonical_url": parser.canonical_url,
            },
        },
        warnings,
    )


@ToolRegistry.register_tool(name="news_crawler")
@tool("news_crawler", args_schema=NewsCrawlerInput)
def news_crawler(url: str) -> str:
    """Fetch and extract a static article or listing page as structured JSON."""

    started = perf_counter()
    normalized_url, validation_error = normalize_news_url(url)
    if validation_error:
        return failure_result(
            "invalid_input",
            code="invalid_url",
            message=validation_error,
            tool_name="news_crawler",
        ).to_json()
    normalized_url, network_error = validate_external_url(normalized_url)
    if network_error:
        return failure_result(
            "blocked",
            code="outbound_url_blocked",
            message=network_error,
            tool_name="news_crawler",
        ).to_json()

    cached_result = get_cached(f"news_crawler:{normalized_url}")
    if isinstance(cached_result, str):
        return cached_result

    headers = {
        "User-Agent": "Mozilla/5.0 AgentFlowNewsCrawler/2.0",
        "Accept": "text/html,application/xhtml+xml",
    }

    current_url = normalized_url
    response = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            for attempt in range(MAX_TRANSIENT_ATTEMPTS):
                try:
                    response = requests.get(
                        current_url,
                        headers=headers,
                        timeout=REQUEST_TIMEOUT,
                        allow_redirects=False,
                    )
                    break
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == MAX_TRANSIENT_ATTEMPTS - 1:
                        raise
                    transient_backoff(attempt)
            status_code = getattr(response, "status_code", None)
            if status_code not in {301, 302, 303, 307, 308}:
                break
            response_headers = getattr(response, "headers", {})
            location = response_headers.get("location") if isinstance(response_headers, Mapping) else None
            if not location:
                break
            next_url, redirect_error = validate_external_url(urljoin(current_url, location))
            if redirect_error:
                return failure_result(
                    "blocked",
                    code="redirect_target_blocked",
                    message=redirect_error,
                    tool_name="news_crawler",
                    source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
                    metadata={"duration_ms": round((perf_counter() - started) * 1000)},
                ).to_json()
            current_url = next_url
        else:
            return failure_result(
                "http_error",
                code="too_many_redirects",
                message=f"Request exceeded the {MAX_REDIRECTS} redirect limit.",
                tool_name="news_crawler",
                source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
                metadata={"duration_ms": round((perf_counter() - started) * 1000)},
            ).to_json()
    except requests.Timeout:
        return failure_result(
            "timeout",
            code="fetch_timeout",
            message=f"Timed out while fetching '{normalized_url}'.",
            retryable=True,
            tool_name="news_crawler",
            source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()
    except requests.RequestException as exc:
        return failure_result(
            "http_error",
            code="fetch_failed",
            message=str(exc),
            retryable=True,
            tool_name="news_crawler",
            source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    if response is None:
        return failure_result(
            "internal_error",
            code="missing_crawler_response",
            message="Crawler returned no HTTP response.",
            tool_name="news_crawler",
        ).to_json()

    status_code = getattr(response, "status_code", None)
    final_url = current_url
    response_url = getattr(response, "url", None)
    if isinstance(response_url, str):
        final_url = response_url
    headers_map = getattr(response, "headers", {})
    if not isinstance(headers_map, Mapping):
        headers_map = {}
    content_type = str(headers_map.get("content-type", "")).split(";", 1)[0].strip().lower()
    source = SourceMetadata(
        requested_url=normalized_url,
        final_url=final_url,
        status_code=status_code if isinstance(status_code, int) else None,
        content_type=content_type or None,
    )

    if not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
        retryable = status_code in {408, 425, 429} or (isinstance(status_code, int) and status_code >= 500)
        return failure_result(
            "http_error",
            code="unexpected_http_status",
            message=f"Failed to fetch URL (HTTP status {status_code}).",
            retryable=retryable,
            tool_name="news_crawler",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    raw_content = getattr(response, "content", None)
    if isinstance(raw_content, bytes) and len(raw_content) > MAX_RESPONSE_BYTES:
        return failure_result(
            "blocked",
            code="response_too_large",
            message=f"Response exceeds the {MAX_RESPONSE_BYTES} byte limit.",
            tool_name="news_crawler",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    if content_type and "html" not in content_type and "xhtml" not in content_type:
        return failure_result(
            "blocked",
            code="unsupported_content_type",
            message=f"Expected HTML but received '{content_type}'.",
            tool_name="news_crawler",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    html = getattr(response, "text", "")
    try:
        status, data, warnings = _extract_page(normalized_url, html)
    except Exception as exc:
        return failure_result(
            "parse_error",
            code="html_parse_failed",
            message=str(exc),
            tool_name="news_crawler",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    metadata = {
        "duration_ms": round((perf_counter() - started) * 1000),
        "extractor": "agentflow_static_html_v2",
        "paragraph_count": len(data.get("paragraphs", [])),
        "item_count": len(data.get("items", [])),
        "content_length": len(data.get("text", "")),
        "warnings": warnings,
    }
    if status == "empty":
        return failure_result(
            "empty",
            code="no_extractable_content",
            message="The page did not contain enough extractable content.",
            tool_name="news_crawler",
            source=source,
            metadata=metadata,
        ).model_copy(update={"data": data}).to_json()

    result = success_result(
        data,
        tool_name="news_crawler",
        source=source,
        metadata=metadata,
    ).to_json()
    set_cached(f"news_crawler:{normalized_url}", result)
    return result
