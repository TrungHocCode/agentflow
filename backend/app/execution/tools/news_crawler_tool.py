"""Static-first web crawler with quality-aware browser rendering fallback."""

from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urldefrag, urljoin, urlparse

import requests  # noqa: F401 - preserves the established crawler mock target used by Tool Lab tests.
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import (
    ToolResult,
    failure_result,
    parse_tool_result,
    success_result,
)
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
TEXT_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote",
                   "td", "th", "dt", "dd", "caption"}
BOILERPLATE_MARKERS = re.compile(
    r"(?:^|[-_\s])(?:breadcrumb|cookie|consent|advert|ads|social|share|related|comments?|sidebar|"
    r"pagination|subscribe|popup|modal|promo|menu)(?:$|[-_\s])",
    re.IGNORECASE,
)

# Embedded structured data (JSON-LD, __NEXT_DATA__, application/json) often holds
# the facts a client-rendered page shows, so extract its meaningful scalars.
JSON_SCRIPT_TYPES = {"application/ld+json", "application/json"}
JSON_FACT_KEYS = {
    "name", "title", "price", "amount", "description", "currency", "plan", "product",
    "offer", "value", "label", "text", "sku", "category", "feature", "tier", "billing",
}
PRICE_PATTERN = re.compile(r"[$€£]\s?\d")
MAX_EMBEDDED_FACTS = 400
MAX_EMBEDDED_TEXT_CHARS = 8000


def embedded_json_facts(payload: Any, max_items: int = MAX_EMBEDDED_FACTS) -> list[str]:
    """Collect bounded, meaningful scalars from embedded JSON without keeping the raw blob."""

    facts: list[str] = []

    def walk(node: Any, key: str | None, depth: int) -> None:
        if len(facts) >= max_items or depth > 12:
            return
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, str(child_key).lower(), depth + 1)
        elif isinstance(node, list):
            for child in node[:50]:
                walk(child, key, depth + 1)
        elif node is None or isinstance(node, bool):
            return
        else:
            text = str(node).strip()
            if not text or len(text) > 300:
                return
            if key in JSON_FACT_KEYS or PRICE_PATTERN.search(text):
                facts.append(text)

    walk(payload, None, 0)
    return facts


class NewsCrawlerInput(BaseModel):
    url: str = Field(description="The web page URL to crawl.")
    mode: Literal["auto", "static", "browser"] = Field(
        default="auto",
        description="Automatically fall back to browser rendering, use static HTTP only, or render directly.",
    )
    max_articles: int = Field(
        default=3,
        ge=0,
        le=5,
        description=(
            "For a listing page, fetch up to this many linked article pages. Set to 0 to return only the listing. "
            "Article fetching is bounded to one level and at most five pages."
        ),
    )
    article_mode: Literal["auto", "static", "browser"] = Field(
        default="static",
        description=(
            "Fetch linked articles with static HTTP by default to limit latency. "
            "Choose auto or browser only when linked articles require JavaScript rendering."
        ),
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
        self.embedded_data: list[str] = []
        self._script_json: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}

        if tag == "script" and not self._skip_depth:
            if attrs_map.get("type", "").lower() in JSON_SCRIPT_TYPES:
                self._script_json = []
                return

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
        if tag == "script" and self._script_json is not None:
            self._capture_embedded_json("".join(self._script_json))
            self._script_json = None
            return
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
        if self._script_json is not None:
            self._script_json.append(data)
            return
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

    def _capture_embedded_json(self, text: str) -> None:
        text = text.strip()
        if not text or len(text) > 500_000:
            return
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return
        for fact in embedded_json_facts(payload):
            if len(self.embedded_data) >= MAX_EMBEDDED_FACTS:
                break
            self.embedded_data.append(fact)


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
    return (parsed.hostname or "").lower() == "news.ycombinator.com" and parsed.path in {"", "/"}


def _build_listing_items(parser: HTMLContentExtractor, base_url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_host = (urlparse(base_url).hostname or "").lower()
    utility_labels = {
        "ask",
        "classic",
        "comments",
        "discuss",
        "jobs",
        "login",
        "new",
        "newest",
        "past",
        "show",
        "submit",
    }
    for link in parser.links:
        text = link["text"].strip()
        href = urljoin(base_url, link["href"])
        href, _ = urldefrag(href)
        parsed = urlparse(href)
        if (
            len(text) < 5
            or text.casefold() in utility_labels
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or href in seen
        ):
            continue
        # Hacker News discussion/navigation links are not article sources.
        if base_host == "news.ycombinator.com" and (
            (parsed.hostname or "").lower() == "news.ycombinator.com"
            or parsed.path.lower() in {"/login", "/submit"}
        ):
            continue
        seen.add(href)
        items.append({"rank": len(items) + 1, "title": text, "url": href})
    return items[:50]


def _crawl_listing_articles(
    listing_result: ToolResult,
    *,
    max_articles: int,
    article_mode: Literal["auto", "static", "browser"],
) -> str:
    """Fetch a bounded number of article links from a successfully parsed listing."""

    if max_articles == 0 or not isinstance(listing_result.data, dict):
        return listing_result.to_json()

    listing_data = dict(listing_result.data)
    if listing_data.get("content_type") != "listing":
        return listing_result.to_json()

    items = listing_data.get("items") or []
    selected_items = [item for item in items if isinstance(item, dict)][:max_articles]
    if not selected_items:
        return listing_result.to_json()

    articles: list[dict[str, Any]] = []
    warnings = list(listing_result.metadata.warnings)
    success_count = 0

    for item in selected_items:
        article_url = str(item.get("url") or "")
        article_title = str(item.get("title") or "Untitled article")
        safe_url, url_error = validate_external_url(article_url)
        if url_error or not safe_url:
            articles.append(
                {
                    "rank": item.get("rank"),
                    "title": article_title,
                    "requested_url": article_url,
                    "status": "blocked",
                    "text": "",
                    "warning": url_error or "Article URL is not allowed.",
                }
            )
            continue

        try:
            raw_result = crawl_url(safe_url, article_mode, extract_page=_extract_page)
            article_result = parse_tool_result(raw_result, tool_name="news_crawler")
        except Exception:  # Keep one bad linked page from hiding other evidence.
            articles.append(
                {
                    "rank": item.get("rank"),
                    "title": article_title,
                    "requested_url": safe_url,
                    "status": "internal_error",
                    "text": "",
                    "warning": "Article fetch failed due to an unexpected crawler error.",
                }
            )
            continue

        article_data = article_result.data if isinstance(article_result.data, dict) else {}
        article_text = str(article_data.get("text") or "").strip()
        is_article = (
            article_data.get("content_type") == "article"
            and len(article_text) >= MIN_ARTICLE_TEXT_LENGTH
        )
        source = article_result.source.model_dump(exclude_none=True) if article_result.source else {}
        record: dict[str, Any] = {
            "rank": item.get("rank"),
            "title": str(article_data.get("title") or article_title),
            "requested_url": safe_url,
            "final_url": source.get("final_url") or safe_url,
            "status": (
                article_result.status
                if is_article or not article_result.ok
                else "not_article"
            ),
            "text": article_text if is_article else "",
            "metadata": article_data.get("metadata") or {},
            "source": source,
        }
        if article_result.error:
            record["warning"] = article_result.error.message
            record["error"] = article_result.error.model_dump()
        elif not is_article:
            record["warning"] = "Linked page did not yield enough article text."
        elif article_result.metadata.warnings:
            record["warning"] = "; ".join(article_result.metadata.warnings)
        articles.append(record)
        if is_article:
            success_count += 1

    listing_data["articles"] = articles
    listing_data["article_count"] = success_count
    if success_count < len(selected_items):
        warnings.append(
            f"Could not extract article content from {len(selected_items) - success_count} selected link(s)."
        )
    metadata = listing_result.metadata.model_dump(exclude_none=True)
    metadata.update({
        "followed_link_count": len(selected_items),
        "article_count": success_count,
        "article_mode": article_mode,
        "warnings": list(dict.fromkeys(warnings)),
    })

    if success_count == 0:
        # Child link failures are observations about the links, not about the
        # parent page: keep the parent verdict and record them as warnings.
        warnings.append(
            "None of the selected links produced usable article text; "
            "the listing content above is still returned."
        )
    metadata = listing_result.metadata.model_dump(exclude_none=True)
    metadata.update({
        "followed_link_count": len(selected_items),
        "article_count": success_count,
        "article_mode": article_mode,
        "warnings": list(dict.fromkeys(warnings)),
    })

    has_partial_article = any(article.get("status") == "partial" for article in articles)
    status = "partial" if has_partial_article else "success"
    return success_result(
        listing_data,
        tool_name="news_crawler",
        source=listing_result.source,
        metadata=metadata,
        status=status,
    ).to_json()


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
    # Block-level extraction can miss content rendered in generic containers (e.g. pricing
    # cards inside <div>/<span>); use the full visible text when it is substantially richer.
    if len(fallback_body) >= MIN_ARTICLE_TEXT_LENGTH and len(fallback_body) > len(body) * 1.3:
        body = fallback_body
        markdown = fallback_body
        paragraphs = [fallback_body]
    embedded_text = HTMLContentExtractor._normalize_text(" ".join(parser.embedded_data))
    if embedded_text:
        # Embedded structured data is high-signal; keep it even when it is short.
        embedded_text = embedded_text[:MAX_EMBEDDED_TEXT_CHARS]
        body = f"{body}\n\n{embedded_text}".strip()
        markdown = f"{markdown}\n\n{embedded_text}".strip()
        paragraphs = [*paragraphs, embedded_text]
    links = _build_listing_items(parser, url)
    warnings: list[str] = []

    # A nav menu is not a listing: require the page body to be mostly link text,
    # and never call a page with a substantial semantic (<main>/<article>) region
    # a listing. Raw link counts alone misfire on pricing/nav pages.
    link_text_chars = sum(len(str(item.get("title") or "")) for item in links)
    link_dense = bool(body) and link_text_chars > len(body) * 0.4
    is_listing = (_is_hacker_news_listing(url) and bool(links)) or (
        len(links) >= 10 and len(body) < 6000 and len(paragraphs) < 8
        and not use_semantic_region and link_dense
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
            "text": body,
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
def news_crawler(
    url: str,
    mode: Literal["auto", "static", "browser"] = "auto",
    max_articles: int = 3,
    article_mode: Literal["auto", "static", "browser"] = "static",
) -> str:
    """Extract a page and, for listings, fetch a bounded number of linked articles."""

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

    raw_result = crawl_url(normalized_url, mode, extract_page=_extract_page)
    page_result = parse_tool_result(raw_result, tool_name="news_crawler")
    if not page_result.ok:
        return raw_result
    return _crawl_listing_articles(
        page_result,
        max_articles=max_articles,
        article_mode=article_mode,
    )
