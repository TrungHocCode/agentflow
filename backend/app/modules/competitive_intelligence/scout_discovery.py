"""Code-owned, search-grounded Scout discovery for CI setup candidates.

The Scout does not call a model. It builds bounded ``web_search_batch`` queries from the
user prompt, then ranks and dedupes the returned domains deterministically. Candidates
carry ``web_search`` provenance so the UI can distinguish them from model memory or
owner-supplied URLs. Nothing here is persisted or approved.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from app.execution.tools.base import ToolRegistry
from app.modules.competitive_intelligence.models import public_url
from app.modules.competitive_intelligence.setup_recommend import RecommendedProduct, RecommendedSource
from app.shared.collection_scope import normalized_url, same_site

MAX_BATCH_QUERIES = 5
MAX_RESULTS_PER_QUERY = 5
MAX_PRODUCTS = 10
MAX_SOURCES_PER_DOMAIN = 5
MAX_TOOL_CALLS_PER_STAGE = 2

DIMENSION_QUERY_TERMS: dict[str, str] = {
    "pricing": "pricing page",
    "features": "features",
    "integrations": "integrations",
    "release_notes": "release notes",
}
_DIMENSION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pricing": ("pricing", "price", "plans", "cost"),
    "features": ("features", "feature", "capabilities", "functionality"),
    "integrations": ("integrations", "integration", "connectors"),
    "release_notes": ("release notes", "releases", "changelog", "what's new"),
}
_GENERIC_CAPITALIZED = {
    "A", "An", "Build", "Compare", "Competitor", "Competitors", "Find", "Focus", "I",
    "Make", "Monitor", "My", "Our", "Plan", "Pricing", "Product", "Products", "The",
    "Track", "Tracking", "Use", "We",
}
_STOPWORDS = {
    "about", "and", "are", "compare", "competitor", "competitors", "for", "from", "into",
    "make", "my", "of", "our", "please", "products", "that", "the", "their", "track",
    "want", "with",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&.+_-]*")
_TOKEN = re.compile(r"[a-z0-9]+")

SearchToolCaller = Callable[[list[str], int], Any | Awaitable[Any]]


async def _default_search_tool_caller(queries: list[str], max_results_per_query: int) -> Any:
    tool = ToolRegistry.get_tool("web_search_batch")
    return await asyncio.to_thread(
        tool.invoke,
        {"queries": queries, "max_results_per_query": max_results_per_query},
    )


def _extract_records(raw: Any) -> list[dict[str, Any]]:
    """Accept the batch tool's JSON string, a ToolResult, or a plain result mapping."""

    if hasattr(raw, "data"):
        raw = raw.data
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if isinstance(raw, list):
        return [record for record in raw if isinstance(record, dict)]
    if not isinstance(raw, dict):
        return []
    data = raw.get("data", raw)
    results = data.get("results") if isinstance(data, dict) else None
    if isinstance(results, list):
        return [record for record in results if isinstance(record, dict)]
    results = raw.get("results")
    return [record for record in results if isinstance(record, dict)] if isinstance(results, list) else []


def _dimensions(prompt: str) -> list[str]:
    lowered = prompt.casefold()
    return [
        dimension
        for dimension, keywords in _DIMENSION_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]


def _product_terms(prompt: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    dimension_words = {word for words in _DIMENSION_KEYWORDS.values() for word in words}
    for match in _WORD.finditer(prompt):
        token = match.group()
        key = token.casefold()
        if (not token[0].isupper() or token in _GENERIC_CAPITALIZED or key in seen
                or token in dimension_words):
            continue
        seen.add(key)
        terms.append(token)
    return terms[:4]


def _fallback_keywords(prompt: str) -> list[str]:
    keywords: list[str] = []
    for token in _TOKEN.findall(prompt.casefold()):
        if len(token) > 1 and token not in _STOPWORDS and token not in keywords:
            keywords.append(token)
    return keywords[:5]


def _ranking_terms(prompt: str, product_terms: list[str]) -> set[str]:
    terms = {token.casefold() for token in product_terms}
    for dimension in _dimensions(prompt):
        terms.update(_DIMENSION_KEYWORDS[dimension])
    if not terms:
        terms.update(_fallback_keywords(prompt))
    return terms


def _registrable_domain(value: str) -> str:
    try:
        host = (urlsplit(value).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _domain_host(value: str) -> str:
    raw = value.strip()
    try:
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return ""
    return (parsed.hostname or "").casefold().rstrip(".")


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(value.casefold()))


def _product_name(title: str, domain: str) -> str:
    cleaned = " ".join(str(title or "").split())
    candidate = re.split(r"\s+[|:–—-]\s+", cleaned, maxsplit=1)[0].strip()
    if candidate.casefold() in {"home", "official site", "plans", "pricing", "welcome"}:
        candidate = ""
    if len(candidate) < 2:
        candidate = domain.split(".")[0].replace("-", " ").title()
    return candidate[:200]


class ScoutDiscovery:
    """Deterministic staged discovery over the registered batch search tool."""

    def __init__(self, search_tool_caller: SearchToolCaller | None = None) -> None:
        self.search_tool_caller = search_tool_caller or _default_search_tool_caller
        self.warnings: list[str] = []
        self._stage_calls = 0

    def _reset_stage(self) -> None:
        self.warnings = []
        self._stage_calls = 0

    def _warn(self, message: str) -> None:
        if len(self.warnings) < 16:
            self.warnings.append(message[:500])

    async def _search(self, queries: list[str], max_results: int) -> list[dict[str, Any]]:
        if self._stage_calls >= MAX_TOOL_CALLS_PER_STAGE:
            self._warn("Discovery tool-call cap reached for this stage.")
            return []
        self._stage_calls += 1
        try:
            raw = self.search_tool_caller(list(queries), max_results)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # A provider failure yields no candidates, not a 500.
            self._warn(f"Web search failed: {exc}")
            return []
        return _extract_records(raw)

    def _competitor_queries(self, prompt: str) -> list[str]:
        terms = _product_terms(prompt)
        dimensions = _dimensions(prompt)
        queries: list[str] = []
        if terms:
            queries.append(" ".join(terms[:4] + ["competitors", "alternatives"]))
            if dimensions:
                queries.append(" ".join(terms[:3] + [DIMENSION_QUERY_TERMS[dimensions[0]]]))
            else:
                queries.append(" ".join(terms[:4] + ["official website"]))
        else:
            keywords = _fallback_keywords(prompt)
            if keywords:
                queries.append(" ".join(keywords[:5] + ["competitors"]))
            if len(keywords) >= 3:
                queries.append(" ".join(keywords[:3] + ["alternatives"]))
        deduped: list[str] = []
        for query in queries:
            normalized = " ".join(query.split())[:300]
            if len(normalized) >= 2 and normalized.casefold() not in {
                item.casefold() for item in deduped
            }:
                deduped.append(normalized)
        return deduped[:2]

    async def stage_a(self, prompt: str) -> list[RecommendedProduct]:
        """Discover competitor candidates from up to two bounded search queries."""

        self._reset_stage()
        queries = self._competitor_queries(prompt)
        if not queries:
            self._warn("Could not derive competitor search terms from the prompt.")
            return []
        records = await self._search(queries, MAX_RESULTS_PER_QUERY)
        if not records:
            self._warn("No web search results were returned for competitor discovery.")
            return []

        ranking_terms = _ranking_terms(prompt, _product_terms(prompt))
        ranked: list[tuple[int, int, str, dict[str, Any], set[str]]] = []
        for index, record in enumerate(records):
            domain = _registrable_domain(str(record.get("url") or ""))
            if not domain:
                continue
            matched = _tokens(
                f"{record.get('title') or ''} {record.get('snippet') or ''} {record.get('url') or ''}"
            ) & ranking_terms
            ranked.append((len(matched), -index, domain, record, matched))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

        products: list[RecommendedProduct] = []
        seen_domains: set[str] = set()
        for score, _, domain, record, matched in ranked:
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            rationale = "Found via web search"
            if matched:
                rationale += f" (keywords: {', '.join(sorted(matched)[:5])})"
            snippet = " ".join(str(record.get("snippet") or "").split())
            if snippet:
                rationale += f": {snippet[:240]}"
            products.append(
                RecommendedProduct(
                    name=_product_name(str(record.get("title") or ""), domain),
                    kind="competitor",
                    company="",
                    official_website=f"https://{domain}/",
                    sources=[],
                    rationale=rationale[:500],
                    provenance="web_search",
                )
            )
            if len(products) >= MAX_PRODUCTS:
                break
        if len(products) < MAX_PRODUCTS and len(seen_domains) > len(products):
            self._warn("Truncated competitor candidates to one per domain.")
        if not products:
            self._warn("Search results did not contain usable competitor domains.")
        return products

    async def stage_b(self, domains: list[str], dimension: str) -> dict[str, list[RecommendedSource]]:
        """Discover same-site source URLs for the picked domains and one dimension."""

        self._reset_stage()
        if dimension not in DIMENSION_QUERY_TERMS:
            self._warn(f"Unsupported discovery dimension: {dimension}.")
            return {}
        picked: list[tuple[str, str]] = []
        seen_hosts: set[str] = set()
        for domain in domains:
            host = _domain_host(domain)
            if host and host not in seen_hosts:
                seen_hosts.add(host)
                picked.append((domain, host))
        if len(picked) > MAX_BATCH_QUERIES:
            self._warn(f"Searched the first {MAX_BATCH_QUERIES} domains; narrow the selection for the rest.")
            picked = picked[:MAX_BATCH_QUERIES]
        if not picked:
            self._warn("No valid domains were provided for source discovery.")
            return {}

        queries = [f"site:{host} {DIMENSION_QUERY_TERMS[dimension]}" for _, host in picked]
        records = await self._search(queries, MAX_RESULTS_PER_QUERY)
        found: dict[str, list[RecommendedSource]] = {domain: [] for domain, _ in picked}
        seen_urls: set[tuple[str, str]] = set()
        for record in records:
            raw_url = str(record.get("url") or "").strip()
            try:
                host = (urlsplit(raw_url).hostname or "").casefold()
            except ValueError:
                continue
            if not host:
                continue
            matches = [domain for domain, domain_host in picked if same_site(host, domain_host)]
            if not matches:
                continue
            key = matches[0]
            try:
                public_url(raw_url)
            except ValueError:
                self._warn(f"Dropped syntactically invalid URL: {raw_url[:120]}.")
                continue
            identity = (key, normalized_url(raw_url))
            if identity in seen_urls or len(found[key]) >= MAX_SOURCES_PER_DOMAIN:
                continue
            seen_urls.add(identity)
            note = " ".join(str(record.get("title") or record.get("snippet") or "").split())[:500]
            found[key].append(
                RecommendedSource(
                    url=raw_url,
                    kind=dimension,
                    provenance="web_search",
                    note=note,
                )
            )
        if not any(found.values()):
            self._warn("No same-site source URLs were returned for the selected domains.")
        return found
