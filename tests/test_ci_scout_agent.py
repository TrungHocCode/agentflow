"""Scout agent: claim parsing, code checks and fallback to the article path; no network or model."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.competitive_intelligence import scout_agent
from app.modules.competitive_intelligence.scout_agent import discover_competitors, parse_answer
from app.modules.competitive_intelligence.scout_discovery import ScoutDiscovery
from app.modules.competitive_intelligence.setup_recommend import ProductBrief, RecommendedProduct

LIST_URL = "https://reviews.example/best-markdown-note-apps"
LIST_URL_2 = "https://other.example/note-taking-apps-for-mac"
DESCRIPTION = "markdown note-taking app for Mac"


def page(text, links=(), title="Best markdown note taking apps"):
    return {"final_url": "", "title": title, "text": text, "markdown": "", "links": list(links)}


class Harness:
    def __init__(self, pages, lookups=()):
        self.pages = pages
        self.lookups = list(lookups)
        self.queries: list[list[str]] = []

    async def fetch(self, url):
        return self.pages.get(url)

    async def search(self, queries, max_results):
        self.queries.append(queries)
        return self.lookups

    async def run(self, answer, own="NoteNest", status="done", description=DESCRIPTION):
        with patch.object(scout_agent, "_run_worker", AsyncMock(return_value=(answer, status, 3))), \
                patch.object(scout_agent, "autodiscover_tools"):
            return await discover_competitors(own, description, None, "global", llm=object(),
                                              page_fetcher=self.fetch, search=self.search)


class TestScoutAgentChecks(unittest.IsolatedAsyncioTestCase):
    def test_parse_answer_reads_claim_lines_and_ignores_none(self) -> None:
        text = f"PRODUCT: Obsidian | SOURCE: {LIST_URL}\n- **PRODUCT: Bear** | SOURCE: <{LIST_URL_2}>."
        self.assertEqual(parse_answer(text), [("Obsidian", LIST_URL), ("Bear", LIST_URL_2)])
        self.assertEqual(parse_answer("NONE: no page lists any"), [])
        self.assertEqual(parse_answer("PRODUCT: X | SOURCE: not-a-url"), [])

    async def test_name_missing_from_the_cited_page_is_dropped(self) -> None:
        h = Harness({LIST_URL: page("Obsidian is a markdown note taking app for Mac.", ["https://obsidian.md/"])})
        products, _ = await h.run(f"PRODUCT: Obsidian | SOURCE: {LIST_URL}\nPRODUCT: Evernote | SOURCE: {LIST_URL}")
        self.assertEqual([p.name for p in products], ["Obsidian"])

    async def test_off_topic_source_is_dropped(self) -> None:
        h = Harness({LIST_URL: page("Ledger and Trezor are the best wallets.", title="Best crypto wallets")})
        products, _ = await h.run(f"PRODUCT: Ledger | SOURCE: {LIST_URL}")
        self.assertEqual(products, [])

    async def test_own_product_and_description_words_are_dropped(self) -> None:
        h = Harness({LIST_URL: page("NoteNest, Mac and Obsidian are markdown note taking apps.",
                                    ["https://obsidian.md/", "https://notenest.app/"])})
        products, _ = await h.run(
            f"PRODUCT: NoteNest | SOURCE: {LIST_URL}\nPRODUCT: Mac | SOURCE: {LIST_URL}\n"
            f"PRODUCT: Obsidian | SOURCE: {LIST_URL}")
        self.assertEqual([p.name for p in products], ["Obsidian"])

    async def test_website_comes_from_source_links_else_one_batched_lookup(self) -> None:
        lookups = [{"url": "https://bear.app/pricing", "title": "Bear pricing", "snippet": "Bear markdown notes"}]
        h = Harness({LIST_URL: page("Obsidian and Bear are markdown note taking apps for Mac.",
                                    ["https://obsidian.md/download"])}, lookups)
        products, _ = await h.run(f"PRODUCT: Obsidian | SOURCE: {LIST_URL}\nPRODUCT: Bear | SOURCE: {LIST_URL}")
        sites = {p.name: p.official_website for p in products}
        self.assertEqual(sites, {"Obsidian": "https://obsidian.md/", "Bear": "https://bear.app/"})
        self.assertEqual(h.queries, [["Bear pricing"]])

    async def test_product_without_a_website_is_dropped(self) -> None:
        h = Harness({LIST_URL: page("Bear is a markdown note taking app for Mac.")})
        products, _ = await h.run(f"PRODUCT: Bear | SOURCE: {LIST_URL}")
        self.assertEqual(products, [])

    async def test_suggested_when_site_is_linked_or_lookup_passes_or_two_sources(self) -> None:
        lookups = [{"url": "https://craft.do/pricing", "title": "Craft pricing", "snippet": "markdown notes"}]
        h = Harness({LIST_URL: page("Obsidian, Bear and Craft: markdown note taking apps for Mac.",
                                    ["https://obsidian.md/", "https://bear.app/"]),
                     LIST_URL_2: page("Obsidian is a note taking app for Mac.", ["https://obsidian.md/"])}, lookups)
        products, _ = await h.run(
            f"PRODUCT: Obsidian | SOURCE: {LIST_URL}\nPRODUCT: Obsidian | SOURCE: {LIST_URL_2}\n"
            f"PRODUCT: Bear | SOURCE: {LIST_URL}\nPRODUCT: Craft | SOURCE: {LIST_URL}")
        self.assertEqual({p.name: p.suggested for p in products},
                         {"Obsidian": True, "Bear": True, "Craft": True})

    async def test_lookup_without_topic_words_stays_unticked(self) -> None:
        lookups = [{"url": "https://craft.do/pricing", "title": "Craft pricing", "snippet": "notes"}]
        h = Harness({LIST_URL: page("Craft is a note taking app.")}, lookups)
        products, _ = await h.run(f"PRODUCT: Craft | SOURCE: {LIST_URL}", description="")
        self.assertEqual({p.name: p.suggested for p in products}, {"Craft": False})

    async def test_failed_worker_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            await Harness({}).run("", status="failed")


class TestScoutStageAWithAgent(unittest.IsolatedAsyncioTestCase):
    PROMPT = "I make NoteNest, a markdown note-taking app for Mac. Find competitors."

    @staticmethod
    async def reader(prompt):
        return ProductBrief(own_product="NoteNest", named_competitors=[], describes_own_product=True,
                            own_product_description=DESCRIPTION, description_in_english=DESCRIPTION)

    def scout(self, agent):
        self.searches: list[list[str]] = []

        def search(queries, max_results):
            self.searches.append(list(queries))
            return {"data": {"results": []}}

        return ScoutDiscovery(search, brief_reader=self.reader, competitor_agent=agent)

    async def test_agent_products_are_used_and_own_is_kept(self) -> None:
        found = [RecommendedProduct(name="Obsidian", official_website="https://obsidian.md/",
                                    provenance="web_search", suggested=True)]
        agent = AsyncMock(return_value=(found, ["Scout agent used 3 model turn(s)."]))
        scout = self.scout(agent)
        products = await scout.stage_a(self.PROMPT)
        self.assertEqual([(p.name, p.kind) for p in products], [("Obsidian", "competitor"), ("NoteNest", "own")])
        self.assertEqual(self.searches, [])
        self.assertIn("Competitors came from the Scout agent.", scout.warnings)
        self.assertEqual(agent.await_args.args[:2], ("NoteNest", DESCRIPTION))

    async def test_none_exception_and_timeout_fall_back_to_the_article_path(self) -> None:
        for agent in (AsyncMock(return_value=([], ["Scout agent reported no competitors."])),
                      AsyncMock(side_effect=RuntimeError("boom")), AsyncMock(side_effect=TimeoutError())):
            scout = self.scout(agent)
            await scout.stage_a(self.PROMPT)
            self.assertEqual(self.searches[0][0], f"best {DESCRIPTION}")
            self.assertTrue(any("comparison-article path" in w for w in scout.warnings), scout.warnings)

    async def test_without_the_agent_nothing_changes(self) -> None:
        scout = self.scout(None)
        await scout.stage_a(self.PROMPT)
        self.assertEqual(self.searches[0][0], f"best {DESCRIPTION}")
        self.assertFalse(any("Scout agent" in w for w in scout.warnings))

    async def test_named_rivals_skip_the_agent(self) -> None:
        agent = AsyncMock()
        await self.scout(agent).stage_a("I make NoteNest. Track pricing of Notion and Obsidian.")
        agent.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
