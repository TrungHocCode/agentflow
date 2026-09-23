"""Static-first web crawler with quality-aware browser rendering fallback."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import requests  # noqa: F401 - preserves the established crawler mock target used by Tool Lab tests.
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import failure_result
from app.execution.tools.crawler.orchestrator import crawl_url
from app.execution.tools.network_policy import validate_external_url


MIN_ARTICLE_TEXT_LENGTH = 80
SKIPPED_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "noscript",
    "template",
    "svg",
}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
TEXT_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote"}
BOILERPLATE_MARKERS = re.compile(
    r"(?:^|[-_\s])(?:breadcrumb|cookie|consent|advert|ads|social|share|related|comments?|sidebar|"
    r"pagination|subscribe|popup|modal|promo|menu)(?:$|[-_\s])",
    re.IGNORECASE,
)


class NewsCrawlerInput(BaseModel):
    url: str = Field(description="The web page URL to crawl.")
    mode: Literal["auto", "static", "browser"] = Field(
        default="auto",
        description="Automatically fall back to browser rendering, use static HTTP only, or render directly.",
    )


class HTMLContentExtractor(HTMLParser):
    """Small dependency-free extractor that favors semantic article regions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.blocks: list[dict[str, Any]] = []
        self.semantic_blocks: list[dict[str, Any]] = []
        self.fallback_text: list[str] = []
        self.links: list[dict[str, str]] = []
        self.canonical_url: str | None = None
        self._skip_depth = 0
        self._semantic_depth = 0
        self._open_elements: list[tuple[str, bool]] = []
        self._in_title = False
        self._current_block_tag: str | None = None
        self._current_text: list[str] = []
        self._current_link: dict[str, str] | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}

        if self._skip_depth:
            if tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        if self._is_boilerplate(tag, attrs_map):
            self._skip_depth = 0 if tag in VOID_TAGS else 1
            return

        is_semantic = tag in {"main", "article"} or attrs_map.get("role", "").lower() == "main"
        if is_semantic:
            self._semantic_depth += 1
        if tag not in VOID_TAGS:
            self._open_elements.append((tag, is_semantic))

        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs_map.get("name") or attrs_map.get("property") or "").lower()
            content = attrs_map.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        elif tag == "link" and "canonical" in attrs_map.get("rel", "").lower().split():
            self.canonical_url = attrs_map.get("href") or None
        elif tag == "a":
            self._current_link = {"href": attrs_map.get("href", "")}
            self._current_link_text = []

        if tag in TEXT_BLOCK_TAGS and self._current_block_tag is None:
            self._current_block_tag = tag
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth = max(0, self._skip_depth - 1)
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
                block = {"tag": tag, "text": text}
                self.blocks.append(block)
                if self._semantic_depth:
                    self.semantic_blocks.append(block)
            self._current_block_tag = None
            self._current_text = []

        for index in range(len(self._open_elements) - 1, -1, -1):
            if self._open_elements[index][0] == tag:
                closed_elements = self._open_elements[index:]
                del self._open_elements[index:]
                semantic_closed = sum(1 for _, is_semantic in closed_elements if is_semantic)
                self._semantic_depth = max(0, self._semantic_depth - semantic_closed)
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.fallback_text.append(data)
        if self._current_block_tag:
            self._current_text.append(data)
        if self._current_link is not None:
            self._current_link_text.append(data)

    @staticmethod
    def _is_boilerplate(tag: str, attrs: dict[str, str]) -> bool:
        if (
            tag in SKIPPED_TAGS
            or attrs.get("role", "").lower() in {"navigation", "complementary", "contentinfo"}
            or "hidden" in attrs
            or attrs.get("aria-hidden", "").lower() == "true"
        ):
            return True
        class_and_id = f"{attrs.get('class', '')} {attrs.get('id', '')}"
        return bool(BOILERPLATE_MARKERS.search(class_and_id))

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(unescape(value).split())


def normalize_news_url(raw_url: str) -> tuple[str | None, str | None]:
    """Normalize common LLM URL formatting and validate HTTP(S) syntax."""

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
    semantic_text_length = sum(len(block["text"]) for block in parser.semantic_blocks)
    all_text_length = sum(len(block["text"]) for block in parser.blocks)
    use_semantic_region = (
        semantic_text_length >= MIN_ARTICLE_TEXT_LENGTH
        and semantic_text_length >= all_text_length * 0.35
    )
    selected_blocks = parser.semantic_blocks if use_semantic_region else parser.blocks

    deduplicated_blocks: list[dict[str, Any]] = []
    seen_block_text: set[str] = set()
    for block in selected_blocks:
        if block["text"] not in seen_block_text:
            seen_block_text.add(block["text"])
            deduplicated_blocks.append(block)

    paragraphs = [block["text"] for block in deduplicated_blocks[:50]]
    markdown = "\n\n".join(
        f"{'#' * int(block['tag'][1])} {block['text']}" if block["tag"].startswith("h") else block["text"]
        for block in deduplicated_blocks[:50]
    )
    body = "\n\n".join(paragraphs)
    fallback_body = HTMLContentExtractor._normalize_text(" ".join(parser.fallback_text))
    if len(body) < MIN_ARTICLE_TEXT_LENGTH and len(fallback_body) >= MIN_ARTICLE_TEXT_LENGTH:
        body = fallback_body
        markdown = fallback_body
        paragraphs = [fallback_body]
    links = _build_listing_items(parser, url)
    warnings: list[str] = []

    is_listing = (_is_hacker_news_listing(url) and bool(links)) or (
        len(links) >= 10 and len(body) < 6000 and len(paragraphs) < 8
    )
    if is_listing:
        data = {
            "content_type": "listing",
            "title": title or "Untitled listing",
            "text": body,
            "markdown": markdown,
            "paragraphs": paragraphs,
            "items": links,
            "metadata": {
                "author": parser.meta.get("author"),
                "published_at": parser.meta.get("article:published_time") or parser.meta.get("datepublished"),
                "canonical_url": parser.canonical_url,
            },
        }
        if not links:
            warnings.append("Listing page did not contain extractable linked items.")
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
                "markdown": markdown,
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
            "markdown": markdown[:40000],
            "paragraphs": paragraphs[:20],
            "items": [],
            "metadata": {
                "author": parser.meta.get("author"),
                "published_at": parser.meta.get("article:published_time") or parser.meta.get("datepublished"),
                "description": parser.meta.get("description") or parser.meta.get("og:description"),
                "canonical_url": parser.canonical_url,
                "extraction_region": "article" if use_semantic_region else "document",
            },
        },
        warnings,
    )


@ToolRegistry.register_tool(name="news_crawler")
@tool("news_crawler", args_schema=NewsCrawlerInput)
def news_crawler(url: str, mode: Literal["auto", "static", "browser"] = "auto") -> str:
    """Extract an article or listing with static-first fetching and bounded browser fallback."""

    normalized_url, validation_error = normalize_news_url(url)
    if validation_error or not normalized_url:
        return failure_result(
            "invalid_input",
            code="invalid_url",
            message=validation_error or "URL is invalid.",
            tool_name="news_crawler",
        ).to_json()

    normalized_url, network_error = validate_external_url(normalized_url)
    if network_error or not normalized_url:
        return failure_result(
            "blocked",
            code="outbound_url_blocked",
            message=network_error or "URL is not allowed.",
            tool_name="news_crawler",
        ).to_json()

    return crawl_url(normalized_url, mode, extract_page=_extract_page)
