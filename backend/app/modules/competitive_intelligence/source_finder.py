"""Source finder: a model-driven worker looks for one product page, then code verifies what it claims.

The worker (profile ``source_finder``) may only search and read pages. Whatever URL and quote it returns are
re-checked here against a fresh fetch, so an invented URL or quote can never become a finding. A found page is
only a proposal; nothing here adds a source to a watchlist.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.execution.agents.base import WorkerAgent
from app.execution.agents.resolver import GLOBAL_RUNTIME_GUIDANCE, AgentResolver
from app.execution.state import Task
from app.modules.competitive_intelligence.scout_discovery import (
    PageFetcher,
    _default_page_fetcher,
    _page_fits,
)

SOURCE_FINDER_MAX_ITERATIONS = 4
MIN_QUOTE_CHARS = 12

_URL = re.compile(r"https?://[^\s<>\"'\])]+")
_URL_LINE = re.compile(r"URL\s*:\s*(\S+)", re.IGNORECASE)
_QUOTE_LINE = re.compile(r"QUOTE\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)
_NOT_PUBLIC = re.compile(r"NOT\s+PUBLIC\s*:?\s*(.*)", re.IGNORECASE | re.DOTALL)
_QUOTED = re.compile(r"[\"“«](.{%d,}?)[\"”»]" % MIN_QUOTE_CHARS, re.DOTALL)


class SourceFinding(BaseModel):
    """Outcome of one search; ``found`` is true only when every code check passed."""

    product_name: str
    website: str
    dimension: str
    found: bool
    url: str | None = None
    quote: str | None = None
    reason: str = ""
    steps: int = 0


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())


def _parse_answer(answer: str) -> tuple[str | None, str | None, str | None]:
    """Return (url, quote, not_public_reason) from the worker's final text, tolerant of formatting."""
    text = str(answer or "").strip()
    url_match = _URL_LINE.search(text) or _URL.search(text)
    url = (url_match.group(1) if url_match and url_match.re is _URL_LINE else
           url_match.group(0) if url_match else None)
    url = url.strip("*`<>\"'.,;)") if url else None
    if not url or not url.lower().startswith(("http://", "https://")):
        refusal = _NOT_PUBLIC.search(text)
        return None, None, (refusal.group(1).strip() or "not public") if refusal else None
    quote_match = _QUOTE_LINE.search(text)
    raw_quote = quote_match.group(1).strip().splitlines()[0] if quote_match else None
    if raw_quote is None:
        quoted = _QUOTED.search(text)
        raw_quote = quoted.group(1) if quoted else None
    quote = raw_quote.strip().strip("\"“”«»'`*").strip() if raw_quote else None
    return url, quote or None, None


async def _run_worker(product_name: str, website: str, dimension: str, llm: BaseChatModel) -> tuple[str, str, int]:
    """Run the source_finder profile; returns (final answer, worker status, model turns used)."""
    task = Task(id=1, node="source_finder", status="running", max_iterations=SOURCE_FINDER_MAX_ITERATIONS,
                description=f"Find the public {dimension} page of {product_name} on its own website {website}.")
    resolved = await AgentResolver().resolve(task)
    profile = resolved.profile
    worker = WorkerAgent(profile.name, f"{profile.system_prompt}\n\n{GLOBAL_RUNTIME_GUIDANCE}", llm,
                         resolved.tools, mode=profile.mode)
    state: dict[str, Any] = {"messages": [], "plan": [task], "current_task": task, "logs": [],
                             "result_storage": [], "mode": "executing", "metadata": {}}
    update = await worker.execute(state)  # type: ignore[arg-type]
    result = update["result_storage"][0]
    steps = sum(1 for line in update.get("logs", []) if "Invoking LLM" in str(line))
    return str(result.get("result") or ""), str(result.get("status")), steps


async def find_source(product_name: str, website: str, dimension: str, *, llm: BaseChatModel,
                      page_fetcher: PageFetcher | None = None) -> SourceFinding:
    """Find ``product_name``'s own page for ``dimension`` and verify it in code."""
    base = SourceFinding(product_name=product_name, website=website, dimension=dimension, found=False)
    answer, status, steps = await _run_worker(product_name, website, dimension, llm)
    base.steps = steps
    if status == "failed":
        base.reason = f"worker did not finish within {SOURCE_FINDER_MAX_ITERATIONS} steps or failed"
        return base
    url, quote, not_public = _parse_answer(answer)
    if url is None:
        base.reason = f"not public: {not_public}" if not_public is not None else "no URL in the worker's answer"
        return base
    base.url = url
    host = (urlsplit(website if "//" in website else f"//{website}").hostname or "").casefold()
    page = await (page_fetcher or _default_page_fetcher)(url)
    if not page:
        base.reason = "the URL could not be fetched"
    elif not quote or len(quote) < MIN_QUOTE_CHARS:
        base.reason = "no usable quote in the worker's answer"
    elif not _page_fits(dimension, host, page):
        base.reason = "page is off-site, the home page, or does not read like the dimension"
    elif _squash(quote) not in _squash(f"{page.get('text', '')}\n{page.get('markdown', '')}"):
        base.reason = "the quote does not appear on the page"
    else:
        base.found, base.quote = True, quote
    if not base.found:
        base.quote = None
    return base
