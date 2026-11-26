"""source_finder: profile shape and the code checks that stop invented URLs and quotes; no network, no model."""

import os
import sys
import unittest
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.agents.resolver import DEFAULT_AGENT_PROFILES
from app.execution.tools.registry import autodiscover_tools
from app.modules.competitive_intelligence.source_finder import (
    SOURCE_FINDER_MAX_ITERATIONS,
    _parse_answer,
    find_source,
)

QUOTE = "Pro plan costs $12 per month"
PAGE_TEXT = f"Pricing\nFree for everyone. {QUOTE}, billed yearly."


def llm_answering(*replies):
    """A model that returns the given replies in order, repeating the last one."""
    llm = MagicMock()
    calls = iter(range(10**6))

    async def invoke(*args, **kwargs):
        return replies[min(next(calls), len(replies) - 1)]

    llm.bind_tools.return_value = llm
    llm.ainvoke = invoke
    return llm


def fetcher(final_url, text=PAGE_TEXT, home_final="https://acme.com/"):
    """Fake fetcher: root URLs resolve to ``home_final``, any other URL to ``final_url``."""
    async def fetch(url):
        is_home = url.rstrip("/").count("/") == 2
        return {"final_url": home_final if is_home else final_url, "title": "Pricing",
                "text": "Welcome" if is_home else text, "markdown": "", "links": []}
    return fetch


def final(url, quote=QUOTE):
    return AIMessage(content=f"URL: {url}\nQUOTE: {quote}")


class TestSourceFinder(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        autodiscover_tools()

    def test_profile_is_read_only_tool_loop(self) -> None:
        profile = DEFAULT_AGENT_PROFILES["source_finder"]
        self.assertEqual(profile.mode, "tool_loop")
        self.assertEqual(profile.tool_names, ["web_search", "fetch_page"])
        self.assertEqual(SOURCE_FINDER_MAX_ITERATIONS, 4)

    def test_answer_parsing_is_tolerant(self) -> None:
        self.assertEqual(_parse_answer("URL: https://a.com/pricing.\nQUOTE: \"hello there world\"")[:2],
                         ("https://a.com/pricing", "hello there world"))
        self.assertEqual(_parse_answer('See **https://a.com/pricing** - "costs twelve dollars"')[:2],
                         ("https://a.com/pricing", "costs twelve dollars"))
        self.assertEqual(_parse_answer("NOT PUBLIC: sales only"), (None, None, "sales only"))

    async def test_found_and_verified(self) -> None:
        url = "https://acme.com/pricing"
        result = await find_source("Acme", "acme.com", "pricing", llm=llm_answering(final(url)),
                                   page_fetcher=fetcher(url))
        self.assertTrue(result.found)
        self.assertEqual((result.url, result.quote, result.steps), (url, QUOTE, 1))

    async def test_off_site_url_rejected(self) -> None:
        url = "https://blog.example.org/acme-pricing"
        result = await find_source("Acme", "acme.com", "pricing", llm=llm_answering(final(url)),
                                   page_fetcher=fetcher(url))
        self.assertFalse(result.found)
        self.assertIn("off-site", result.reason)

    async def test_home_page_rejected(self) -> None:
        result = await find_source("Acme", "acme.com", "pricing", llm=llm_answering(final("https://acme.com/")),
                                   page_fetcher=fetcher("https://acme.com/"))
        self.assertFalse(result.found)

    async def test_quote_is_optional_and_only_kept_when_on_the_page(self) -> None:
        url = "https://acme.com/pricing"
        bare = await find_source("Acme", "acme.com", "pricing", llm=llm_answering(AIMessage(content=f"URL: {url}")),
                                 page_fetcher=fetcher(url))
        self.assertTrue(bare.found)
        self.assertIsNone(bare.quote)
        invented = await find_source("Acme", "acme.com", "pricing",
                                     llm=llm_answering(final(url, "Enterprise costs $999 per seat")),
                                     page_fetcher=fetcher(url))
        self.assertTrue(invented.found)
        self.assertIsNone(invented.quote)

    async def test_brand_redirect_counts_as_same_site(self) -> None:
        url = "https://www.notion.com/pricing"
        result = await find_source("Notion", "notion.so", "pricing", llm=llm_answering(final(url)),
                                   page_fetcher=fetcher(url, home_final="https://www.notion.com/"))
        self.assertTrue(result.found)

    async def test_unfetchable_url_rejected(self) -> None:
        async def nothing(url):
            return None
        result = await find_source("Acme", "acme.com", "pricing",
                                   llm=llm_answering(final("https://acme.com/made-up")), page_fetcher=nothing)
        self.assertFalse(result.found)

    async def test_not_public(self) -> None:
        result = await find_source("Acme", "acme.com", "pricing",
                                   llm=llm_answering(AIMessage(content="NOT PUBLIC: pricing is by quote only")),
                                   page_fetcher=fetcher("https://acme.com/pricing"))
        self.assertFalse(result.found)
        self.assertIn("pricing is by quote only", result.reason)

    async def test_iteration_cap(self) -> None:
        loop = AIMessage(content="", tool_calls=[{"name": "unknown_tool", "args": {}, "id": "t1"}])
        result = await find_source("Acme", "acme.com", "pricing", llm=llm_answering(loop),
                                   page_fetcher=fetcher("https://acme.com/pricing"))
        self.assertFalse(result.found)
        self.assertEqual(result.steps, SOURCE_FINDER_MAX_ITERATIONS)


if __name__ == "__main__":
    unittest.main()
