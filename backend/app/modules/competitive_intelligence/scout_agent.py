"""Scout agent: a model-driven worker reads comparison pages, then code verifies every product it claims.

The worker (profile ``scout``) may only search and read pages, and answers with ``PRODUCT: <name> | SOURCE: <url>``
lines. Each claim is re-checked here against a fresh fetch of the cited page, so a name taken from memory, an
off-topic page or a namesake website can never reach the user. Nothing here keeps state between calls.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.execution.agents.base import WorkerAgent
from app.execution.agents.resolver import GLOBAL_RUNTIME_GUIDANCE, AgentResolver
from app.execution.state import Task
from app.execution.tools.registry import autodiscover_tools
from app.modules.competitive_intelligence.scout_discovery import (
    MAX_BATCH_QUERIES,
    MAX_PRODUCTS,
    MAX_RESULTS_PER_QUERY,
    _STOPWORDS,
    PageFetcher,
    _compact,
    _registrable_domain,
    _site_of,
    _site_record,
    _tokens,
)
from app.modules.competitive_intelligence.setup_recommend import RecommendedProduct

SCOUT_MAX_ITERATIONS = 6
SCOUT_TIMEOUT_SECONDS = 60
MAX_CLAIMS = 15
MAX_NAME_CHARS = 60

SearchCaller = Callable[[list[str], int], Awaitable[list[dict[str, Any]]]]

_CLAIM = re.compile(r"PRODUCT\s*:\s*(.+?)\s*\|\s*SOURCE\s*:\s*(\S+)", re.IGNORECASE)
_NONE = re.compile(r"^\W*NONE\s*:", re.IGNORECASE | re.MULTILINE)


def parse_answer(answer: str) -> list[tuple[str, str]]:
    """Return (name, source url) pairs from the worker's final text; empty for NONE or unreadable text."""
    claims: list[tuple[str, str]] = []
    for match in _CLAIM.finditer(str(answer or "")):
        name = match.group(1).strip().strip("*`_\"'[]").strip()
        url = match.group(2).strip("*`<>\"'.,;)(")
        if 2 <= len(name) <= MAX_NAME_CHARS and url.lower().startswith(("http://", "https://")):
            claims.append((name, url))
    return claims[:MAX_CLAIMS]


async def _run_worker(description: str, llm: BaseChatModel) -> tuple[str, str, int]:
    """Run the scout profile; returns (final answer, worker status, model turns used)."""
    task = Task(id=1, node="scout", status="running", max_iterations=SCOUT_MAX_ITERATIONS, description=description)
    resolved = await AgentResolver().resolve(task)
    profile = resolved.profile
    worker = WorkerAgent(profile.name, f"{profile.system_prompt}\n\n{GLOBAL_RUNTIME_GUIDANCE}", llm,
                         resolved.tools, mode=profile.mode)
    state: dict[str, Any] = {"messages": [], "plan": [task], "current_task": task, "logs": [],
                             "result_storage": [], "mode": "executing", "metadata": {}}
    update = await worker.execute(state)  # type: ignore[arg-type]
    result = update["result_storage"][0]
    turns = sum(1 for line in update.get("logs", []) if "Invoking LLM" in str(line))
    return str(result.get("result") or ""), str(result.get("status")), turns


def _task_description(own: str | None, description: str, local: str | None, market: str) -> str:
    lines = [f"Find competitor products of the user's product. Category: {description[:200]}."]
    if local:
        lines.append(f"In the user's own words: {local[:200]}.")
    if market == "vn":
        lines.append("The market is Vietnam: search in Vietnamese as well as English.")
    if own:
        lines.append(f"The user's own product is {own[:60]}; do not report it.")
    return " ".join(lines)


def _mentions(name: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


async def discover_competitors(own: str | None, description: str, local_description: str | None = None,
                               market: str = "global", *, llm: BaseChatModel, page_fetcher: PageFetcher,
                               search: SearchCaller) -> tuple[list[RecommendedProduct], list[str]]:
    """Run the Scout worker and return the competitors that pass the code checks, plus warnings.

    Raises on worker failure or timeout; the caller falls back to the article path. An empty list means the
    worker answered NONE or no claim survived.
    """
    autodiscover_tools()  # the CI worker may not have imported the graph, which loads tools
    answer, status, turns = await asyncio.wait_for(
        _run_worker(_task_description(own, description, local_description, market), llm), SCOUT_TIMEOUT_SECONDS)
    warnings = [f"Scout agent used {turns} model turn(s)."]
    if status == "failed":
        raise RuntimeError(f"scout worker did not finish within {SCOUT_MAX_ITERATIONS} steps or failed")
    claims = parse_answer(answer)
    if not claims:
        warnings.append("Scout agent reported no competitors.")
        return [], warnings

    urls = list(dict.fromkeys(url for _, url in claims))
    fetched = await asyncio.gather(*(page_fetcher(url) for url in urls), return_exceptions=True)
    pages = {url: page for url, page in zip(urls, fetched) if isinstance(page, dict)}
    described = f"{description} {local_description or ''}"
    topic = {token for token in _tokens(description) if len(token) >= 3 and token not in _STOPWORDS}
    source_domains = {_registrable_domain(url) for url in pages}
    links = [{"url": link} for page in pages.values() for link in page.get("links", [])
             if _registrable_domain(link) not in source_domains]

    listed_by: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    dropped = 0
    for name, url in claims:
        page = pages.get(url)
        key = _compact(name)
        text = f"{page.get('title', '')}\n{page.get('text', '')}\n{page.get('markdown', '')}" if page else ""
        # The name must be on the cited page (else it came from memory), the page must be about the category,
        # and a name that is the own product or a word of the user's description is not a rival.
        if (not key or not page or not _mentions(name, text) or (topic and not topic & _tokens(text))
                or (own and key == _compact(own)) or _mentions(name, described)):
            dropped += 1
            continue
        names.setdefault(key, name)
        listed_by.setdefault(key, set()).add(_registrable_domain(url))
    if dropped:
        warnings.append(f"Scout agent: dropped {dropped} claim(s) not confirmed by the page they cited.")

    sites = {key: _site_of(names[key], links) for key in names}
    unresolved = [key for key in names if sites[key] is None][:MAX_BATCH_QUERIES]
    lookup_passed: set[str] = set()
    if unresolved:
        records = await search([f"{names[key]} pricing" for key in unresolved], MAX_RESULTS_PER_QUERY)
        for key in unresolved:
            record = _site_record(names[key], records)
            record_text = f"{record.get('title') or ''} {record.get('snippet') or ''}" if record else ""
            # A namesake (an "Atlas" that maps) shares no word with the category.
            if record and topic and not topic & _tokens(record_text):
                record = None
            if record:
                sites[key] = _registrable_domain(str(record.get("url") or ""))
                if topic:
                    lookup_passed.add(key)

    products: list[RecommendedProduct] = []
    seen: set[str] = set()
    for key in sorted(names, key=lambda k: -len(listed_by[k])):
        domain = sites.get(key)
        if not domain or domain in seen or domain in source_domains:
            continue
        seen.add(domain)
        products.append(RecommendedProduct(
            name=names[key], kind="competitor", suggested=len(listed_by[key]) >= 2 or key in lookup_passed,
            company="", official_website=f"https://{domain}/", sources=[], provenance="web_search",
            rationale=f"Listed on {', '.join(sorted(listed_by[key]))} (found by the Scout agent)."[:500]))
        if len(products) >= MAX_PRODUCTS:
            break
    return products, warnings
