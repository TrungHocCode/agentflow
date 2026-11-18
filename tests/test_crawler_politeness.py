"""robots.txt rules and per-site pacing; deterministic fixtures, no network."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

import requests

from app.execution.tools.crawler import orchestrator, politeness
from app.execution.tools.news_crawler_tool import _extract_page


def robots(text: str, status_code: int = 200) -> MagicMock:
    return MagicMock(status_code=status_code, text=text, headers={})


class TestPoliteness(unittest.TestCase):
    def setUp(self) -> None:
        politeness.reset()
        self.addCleanup(politeness.reset)
        env = patch.dict(os.environ, {"AGENTFLOW_CRAWLER_POLITENESS_ENABLED": "true"})
        env.start()
        self.addCleanup(env.stop)

    def test_disallowed_page_is_not_fetched(self) -> None:
        rules = robots("User-agent: AgentFlowResearchCrawler\nDisallow: /pricing\n")
        with patch.object(politeness.requests, "get", return_value=rules) as get:
            result = json.loads(orchestrator.crawl_url("https://example.com/pricing", "auto",
                                                       extract_page=_extract_page))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"]["code"], "robots_disallowed")
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], "https://example.com/robots.txt")

    def test_rules_for_other_agents_and_missing_files_allow(self) -> None:
        with patch.object(politeness.requests, "get", return_value=robots("User-agent: BadBot\nDisallow: /\n")):
            self.assertTrue(politeness.admit("https://a.example/pricing"))
        with patch.object(politeness.requests, "get", return_value=robots("", 404)):
            self.assertTrue(politeness.admit("https://b.example/pricing"))
        with patch.object(politeness.requests, "get", side_effect=requests.ConnectionError("down")):
            self.assertTrue(politeness.admit("https://c.example/pricing"))

    def test_rules_are_cached_and_crawl_delay_paces_one_site(self) -> None:
        rules = robots("User-agent: *\nCrawl-delay: 3\n")
        with patch.object(politeness.requests, "get", return_value=rules) as get, \
                patch.object(politeness.time, "sleep") as sleep:
            politeness.admit("https://example.com/pricing")
            politeness.admit("https://example.com/features")
            politeness.admit("https://other.example/pricing")
        self.assertEqual(get.call_count, 2)  # one robots.txt per site
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 3, delta=0.5)

    def test_disabled_politeness_fetches_nothing(self) -> None:
        with patch.dict(os.environ, {"AGENTFLOW_CRAWLER_POLITENESS_ENABLED": "false"}), \
                patch.object(politeness.requests, "get") as get:
            self.assertTrue(politeness.admit("https://example.com/pricing"))
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
