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
from urllib.parse import urljoin, urlsplit

from app.execution.tools.base import ToolRegistry
from app.modules.competitive_intelligence.models import public_url
from app.modules.competitive_intelligence.setup_recommend import RecommendedProduct, RecommendedSource
from app.shared.collection_scope import normalized_url, same_site
from app.shared.errors import DependencyUnavailableError

MAX_BATCH_QUERIES = 5
MAX_NAMED_QUERIES = 5
MAX_RESULTS_PER_QUERY = 5
MAX_PRODUCTS = 10
MAX_SOURCES_PER_DOMAIN = 5
MAX_TOOL_CALLS_PER_STAGE = 2
MAX_LISTICLES = 4

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
    "A", "An", "And", "Build", "Check", "Compare", "Competitor", "Competitors", "Find", "Focus", "Follow",
    "Help", "Hi", "Hello", "How", "I", "Keep", "Make", "Monitor", "My", "Need", "Our", "Plan", "Please",
    "Pricing", "Product", "Products", "Show", "Tell", "The", "They", "Track", "Tracking", "Use", "Want",
    "Watch", "We", "What", "Who", "Why",
    # Platforms and categories, not products.
    "AI", "Android", "App", "Chrome", "Linux", "Mac", "SaaS", "Web", "Windows",
    "API", "ATS", "B2B", "B2C", "CMS", "CRM", "ERP", "HR", "LMS", "POS", "SEO", "SMB",
}
_STOPWORDS = {
    "about", "and", "are", "compare", "competitor", "competitors", "for", "from", "into",
    "make", "my", "of", "our", "please", "products", "that", "the", "their", "track",
    "want", "with",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&.+_-]*")
_OWN_PRODUCT = re.compile(
    r"\b(?i:i|we)(?i:'m|'re| am| are)?\s+(?i:make|making|build|building|built|run|running|sell|selling|own|develop|"
    r"developing|created?)\s+([A-Z][A-Za-z0-9&.+_-]*)")
# Editorial pages rank highly for competitor searches but are not products to track.
_ARTICLE_TITLE = re.compile(
    r"\b(?:best|top\s+\d+|\d+\s+top|alternatives?|vs\.?|versus|compared|comparison|reviews?|ranked)\b", re.IGNORECASE)
_NAME_SEPARATOR = re.compile(r"\s*[:|]\s+|\s+[\u2013\u2014-]\s+")
_TRAILING_PAGE_WORD = re.compile(r"\s+(?:pricing|plans?|prices?|features?|integrations?)$", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")
# The category in "I make NoteNest, a markdown note-taking app for Mac." -> "markdown note-taking app for Mac".
_CATEGORY = re.compile(r"\b(?:an?|the)\s+([^.;!?]{3,80}?)\s*(?:[.;!?]|$)", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,4}\s+(.+)$")
_LIST_NUMBER = re.compile(r"^(?:#?\d{1,2}[.):]?|No\.\s*\d{1,2})\s+")
# Headings in "best X" articles that are sections, not products. Unverified names are dropped later anyway.
_SECTION_FIRST_WORDS = {
    "about", "alternatives", "are", "best", "bottom", "can", "comments", "comparison", "conclusion", "cons",
    "discover", "do", "does", "faq", "faqs", "features", "final", "frequently", "how", "integrations", "is", "key",
    "methodology", "more", "not", "other", "our", "overview", "pricing", "pros", "related", "requirements",
    "share", "should", "summary", "table", "the", "tl;dr", "tools", "top", "verdict", "what", "when", "where",
    "which", "who", "why", "worth",
}
# Templates and single forum threads mention a dimension without being the product's page for it.
_NOT_A_SOURCE_PATH = re.compile(r"/(?:templates?|t)/", re.IGNORECASE)

SearchToolCaller = Callable[[list[str], int], Any | Awaitable[Any]]


# Common paths per dimension, tried on the competitor's own site before any web search.
SITE_PATHS: dict[str, tuple[str, ...]] = {
    "pricing": ("/pricing", "/plans", "/compare-plans", "/pricing-plans", "/premium"),
    "features": ("/features", "/product"),
    "integrations": ("/integrations", "/apps", "/marketplace"),
    "release_notes": ("/changelog", "/release-notes", "/releases", "/whats-new"),
}
_PRICING_TEXT = re.compile(
    r"[$€£¥₹₫]\s?\d|\d[\d.,]*\s?(?:USD|EUR|GBP|VND)\b|per (?:month|user|seat|member)|/mo\b|billed", re.IGNORECASE)

PageFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]


async def _default_page_fetcher(url: str) -> dict[str, Any] | None:
    """Fetch one public page with the app's crawler, plus its links; None when it is not a usable page."""
    # Lazy: the crawler modules are heavy.
    from app.execution.tools.crawler.orchestrator import crawl_url
    from app.execution.tools.network_policy import validate_external_url
    from app.execution.tools.news_crawler_tool import HTMLContentExtractor, _extract_page

    safe_url, error = validate_external_url(url)
    if error or not safe_url:
        return None

    def extract_with_links(page_url: str, html: str) -> tuple[str, dict[str, Any], list[str]]:
        status, data, warnings = _extract_page(page_url, html)
        parser = HTMLContentExtractor()
        parser.feed(html)
        parser.close()
        data["links"] = [urljoin(page_url, link["href"]) for link in parser.links[:500]]
        return status, data, warnings

    raw = await asyncio.to_thread(crawl_url, safe_url, "static", extract_page=extract_with_links)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    data = result.get("data") or {}
    return {"final_url": str((result.get("source") or {}).get("final_url") or url),
            "title": str(data.get("title") or ""), "text": str(data.get("text") or ""),
            "markdown": str(data.get("markdown") or ""), "links": list(data.get("links") or [])}


def _page_fits(dimension: str, host: str, page: dict[str, Any]) -> bool:
    """A probed page counts only if it stayed on the site, is not the home page, and reads like the dimension."""
    final = urlsplit(page.get("final_url") or "")
    if not final.hostname or not same_site(final.hostname.casefold(), host) or final.path.strip("/") == "":
        return False
    text = f"{page.get('title', '')}\n{page.get('text', '')[:4000]}"
    if dimension == "pricing":
        return bool(_PRICING_TEXT.search(text))
    return any(keyword in text.casefold() for keyword in _DIMENSION_KEYWORDS[dimension])


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


def _provider_refused(raw: Any) -> bool:
    """Tell a provider failure from a search that honestly found nothing."""

    payload = raw.model_dump() if hasattr(raw, "model_dump") else raw
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return False
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return False
    queries = (payload.get("data") or {}).get("queries") or []
    return not queries or any(record.get("status") != "empty" for record in queries)


def _search_unavailable() -> DependencyUnavailableError:
    return DependencyUnavailableError(
        "The web search provider is not accepting requests right now. "
        "Try again in a few minutes, or add the products and URLs manually.",
        code="search_provider_unavailable",
    )


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
        token = match.group().rstrip("&.+_-")
        key = token.casefold()
        if (not token[0].isupper() or token in _GENERIC_CAPITALIZED or key in seen
                or token in dimension_words):
            continue
        seen.add(key)
        terms.append(token)
    return terms


def _fallback_keywords(prompt: str) -> list[str]:
    keywords: list[str] = []
    for token in _TOKEN.findall(prompt.casefold()):
        if len(token) > 1 and token not in _STOPWORDS and token not in keywords:
            keywords.append(token)
    return keywords[:5]


def _own_product(prompt: str) -> str | None:
    match = _OWN_PRODUCT.search(prompt)
    return match.group(1).rstrip("&.+_-") if match else None


def _category(prompt: str, own: str | None) -> str | None:
    """The product category the user described, searched as "best <category>" when no rival is named."""
    start = prompt.find(own) + len(own) if own and own in prompt else 0
    match = _CATEGORY.search(prompt, start)
    return " ".join(match.group(1).split()) if match else None


def _listed_names(markdown: str) -> list[str]:
    """Product names from the headings of a "best X" article ("## 2. ClickUp", "### Bear Notes")."""
    names: list[str] = []
    for block in markdown.split("\n\n"):
        heading = _HEADING.match(block.strip())
        if not heading:
            continue
        text = _LIST_NUMBER.sub("", heading.group(1).strip())
        text = re.split(r"\s*[(\[]", _NAME_SEPARATOR.split(text, maxsplit=1)[0], maxsplit=1)[0].strip(" .:")
        words = text.split()
        # Product names capitalize every word ("Bear Notes", "iA Writer", "Things 3"); sections do not.
        if (not 1 <= len(words) <= 3 or len(text) > 40 or words[0].casefold() in _SECTION_FIRST_WORDS
                or not all(any(char.isupper() or char.isdigit() for char in word) for word in words)
                or text in names):
            continue
        names.append(text)
    return names


def _site_of(name: str, records: list[dict[str, Any]]) -> str | None:
    """The first result domain that is plausibly the product's own (bear.app for "Bear Notes").

    The domain name must be the product name or part of it; a longer one (craftybase.com for "Craft") is another firm.
    """
    wanted = _compact(name)
    for record in records:
        domain = _registrable_domain(str(record.get("url") or ""))
        label = _compact(domain.split(".")[0]) if domain else ""
        if label and (label == wanted or (len(label) >= 4 and label in wanted)):
            return domain
    return None


def _compact(value: str) -> str:
    return "".join(_TOKEN.findall(value.casefold()))


def _named_term(domain: str, terms: list[str]) -> str | None:
    """Return the prompt's product name when a result's domain is that product's own."""

    # Compare the whole domain too, so a name like "Monday.com" matches monday.com.
    names = {_compact(domain.split(".")[0]), _compact(domain)}
    return next((term for term in terms if _compact(term) in names), None)


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
    candidate = _NAME_SEPARATOR.split(cleaned, maxsplit=1)[0].strip()
    while _TRAILING_PAGE_WORD.search(candidate):
        candidate = _TRAILING_PAGE_WORD.sub("", candidate).strip()
    if candidate.casefold() in {"home", "official site", "plans", "pricing", "welcome"}:
        candidate = ""
    if len(candidate) < 2:
        candidate = domain.split(".")[0].replace("-", " ").title()
    return candidate[:200]


class ScoutDiscovery:
    """Deterministic staged discovery over the registered batch search tool."""

    def __init__(self, search_tool_caller: SearchToolCaller | None = None,
                 page_fetcher: PageFetcher | None = None) -> None:
        self.search_tool_caller = search_tool_caller or _default_search_tool_caller
        self.page_fetcher = page_fetcher or _default_page_fetcher
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
        except Exception as exc:
            raise _search_unavailable() from exc
        if _provider_refused(raw):
            raise _search_unavailable()
        return _extract_records(raw)

    async def _probe_site(self, host: str, dimension: str) -> RecommendedSource | None:
        """Try the site's usual paths for the dimension; the first page that fits wins."""
        for path in SITE_PATHS[dimension]:
            try:
                page = await self.page_fetcher(f"https://{host}{path}")
            except Exception:
                page = None
            if page and _page_fits(dimension, host, page):
                try:
                    url = public_url(page["final_url"])
                except ValueError:
                    continue
                return RecommendedSource(url=url, kind=dimension, provenance="site_crawl",
                                         note=" ".join(page.get("title", "").split())[:500])
        return None

    def _competitor_queries(self, prompt: str) -> list[str]:
        terms = _product_terms(prompt)
        dimensions = _dimensions(prompt)
        own = _own_product(prompt)
        named = [term for term in terms if term != own]
        queries: list[str] = []
        if named:
            # Named competitors: look each one up, since a combined "alternatives" query
            # mostly returns comparison articles about them.
            suffix = DIMENSION_QUERY_TERMS[dimensions[0]] if dimensions else "official website"
            queries = [f"{term} {suffix}" for term in named[:MAX_NAMED_QUERIES]]
            if len(named) > MAX_NAMED_QUERIES:
                self._warn(f"Searched the first {MAX_NAMED_QUERIES} named products; add the rest manually: "
                           f"{', '.join(named[MAX_NAMED_QUERIES:])}.")
        elif category := _category(prompt, own):
            # Nobody named: "best X" articles list the field; stage A reads their headings.
            queries = [f"best {category}", f"{category} alternatives"]
        elif terms:
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
        return deduped[:MAX_NAMED_QUERIES]

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

        terms = _product_terms(prompt)
        own = _own_product(prompt)
        if not [term for term in terms if term != own]:
            listed = await self._listed_products(records, own)
            if listed:
                return self._with_own(listed, own)
        ranking_terms = _ranking_terms(prompt, terms)
        ranked: list[tuple[bool, int, int, str, dict[str, Any], set[str], str | None]] = []
        skipped_articles = 0
        for index, record in enumerate(records):
            domain = _registrable_domain(str(record.get("url") or ""))
            if not domain:
                continue
            named = _named_term(domain, terms)
            title = str(record.get("title") or "")
            # Off-site pages that name a requested product are reviews or namesakes, not other competitors.
            about_named = any(_compact(term) in _compact(title) for term in terms if term != own)
            if named is None and (about_named or _ARTICLE_TITLE.search(title)):
                skipped_articles += 1
                continue
            matched = _tokens(
                f"{record.get('title') or ''} {record.get('snippet') or ''} {record.get('url') or ''}"
            ) & ranking_terms
            ranked.append((named is not None, len(matched), -index, domain, record, matched, named))
        ranked.sort(key=lambda item: item[:3], reverse=True)
        if skipped_articles:
            self._warn(f"Skipped {skipped_articles} comparison or review articles; they are not products to track.")

        products: list[RecommendedProduct] = []
        seen_domains: set[str] = set()
        # Different companies share names (craft.do vs craft.io); pre-pick only the top-ranked site.
        picked_names: set[str] = set()
        shared_names: set[str] = set()
        for _, _, _, domain, record, matched, named in ranked:
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            if named is not None and named in picked_names:
                shared_names.add(named)
            rationale = "Found via web search"
            if matched:
                rationale += f" (keywords: {', '.join(sorted(matched)[:5])})"
            snippet = " ".join(str(record.get("snippet") or "").split())
            if snippet:
                rationale += f": {snippet[:240]}"
            products.append(
                RecommendedProduct(
                    name=named or _product_name(str(record.get("title") or ""), domain),
                    kind="own" if named and named == own else "competitor",
                    suggested=named is not None and named not in shared_names,
                    company="",
                    official_website=f"https://{domain}/",
                    sources=[],
                    rationale=rationale[:500],
                    provenance="web_search",
                )
            )
            if named is not None:
                picked_names.add(named)
            if len(products) >= MAX_PRODUCTS:
                break
        products = self._with_own(products, own)
        if shared_names:
            self._warn(f"Several websites are named {', '.join(sorted(shared_names))}; only the top result is "
                       "ticked. Check each website before tracking.")
        if len(products) < MAX_PRODUCTS and len(seen_domains) > len(products):
            self._warn("Truncated competitor candidates to one per domain.")
        if not products:
            self._warn("Search results did not contain usable competitor domains.")
        return products

    @staticmethod
    def _with_own(products: list[RecommendedProduct], own: str | None) -> list[RecommendedProduct]:
        if own and not any(product.kind == "own" for product in products):
            # The user named their product ("I make InkPad"); approval needs it even without a website.
            if len(products) >= MAX_PRODUCTS:
                spare = [index for index, product in enumerate(products) if not product.suggested]
                products.pop(spare[-1] if spare else -1)
            products.append(RecommendedProduct(name=own, kind="own", suggested=True, company="",
                                               official_website=None, sources=[], provenance="user_supplied",
                                               rationale="Named in your request as your own product."))
        return products

    async def _listed_products(self, records: list[dict[str, Any]], own: str | None) -> list[RecommendedProduct]:
        """Read the top "best X" articles and keep the products they list that have a website of their own."""
        articles: list[dict[str, Any]] = []
        article_domains: set[str] = set()
        for record in records:
            domain = _registrable_domain(str(record.get("url") or ""))
            if domain and domain not in article_domains and _ARTICLE_TITLE.search(str(record.get("title") or "")):
                article_domains.add(domain)
                articles.append(record)
        articles = articles[:MAX_LISTICLES]
        if not articles:
            return []
        pages = await asyncio.gather(*(self.page_fetcher(str(record["url"])) for record in articles),
                                     return_exceptions=True)
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        listed_by: dict[str, list[str]] = {}
        links: list[dict[str, str]] = []
        for record, page in zip(articles, pages):
            if not isinstance(page, dict):
                continue
            links.extend({"url": link} for link in page.get("links", [])
                         if _registrable_domain(link) not in article_domains)
            for name in _listed_names(page.get("markdown", "")):
                key = _compact(name)
                if not key or (own and key == _compact(own)):
                    continue
                names.setdefault(key, name)
                counts[key] = counts.get(key, 0) + 1
                listed_by.setdefault(key, []).append(_registrable_domain(str(record["url"])))
        if not counts:
            self._warn("The comparison articles found did not list readable product names.")
            return []
        # Products listed by several articles first; a name in only one may be a section heading.
        ranked = sorted(counts, key=lambda key: -counts[key])[:MAX_PRODUCTS]
        # The articles usually link each product's own site; search only for the names they do not.
        sites = {key: _site_of(names[key], links) for key in ranked}
        unresolved = [key for key in ranked if sites[key] is None][:MAX_BATCH_QUERIES]
        if unresolved:
            # Pricing pages live on the product's own site; "<name> official website" mostly finds directories.
            lookups = await self._search([f"{names[key]} pricing" for key in unresolved], MAX_RESULTS_PER_QUERY)
            sites.update({key: _site_of(names[key], lookups) for key in unresolved})
        products: list[RecommendedProduct] = []
        seen: set[str] = set()
        for key in ranked:
            domain = sites[key]
            if domain is None or domain in seen or domain in article_domains:
                continue
            seen.add(domain)
            rationale = f"Listed in {counts[key]} comparison article(s): {', '.join(listed_by[key])}."
            products.append(RecommendedProduct(
                name=names[key], kind="competitor", suggested=counts[key] >= 2, company="",
                official_website=f"https://{domain}/", sources=[], provenance="web_search", rationale=rationale[:500],
            ))
        if not products:
            self._warn("No listed product had a website matching its name; add competitors by hand.")
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

        found: dict[str, list[RecommendedSource]] = {domain: [] for domain, _ in picked}
        seen_urls: set[tuple[str, str]] = set()
        # The competitor's own site first: it needs no search provider, which often refuses.
        probed = await asyncio.gather(*(self._probe_site(host, dimension) for _, host in picked))
        for (domain, _), source in zip(picked, probed):
            if source is not None:
                found[domain].append(source)
                seen_urls.add((domain, normalized_url(source.url)))
        picked = [(domain, host) for domain, host in picked if not found[domain]]
        if not picked:
            return found

        queries = [f"site:{host} {DIMENSION_QUERY_TERMS[dimension]}" for _, host in picked]
        try:
            records = await self._search(queries, MAX_RESULTS_PER_QUERY)
        except DependencyUnavailableError:
            if not any(found.values()):
                raise
            self._warn("Web search is unavailable, so some products have no source yet; add their URLs by hand.")
            return found
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
            if _NOT_A_SOURCE_PATH.search(urlsplit(raw_url).path):
                continue
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
