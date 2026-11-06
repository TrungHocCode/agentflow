import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

import requests

from app.core.config import settings
from app.execution.tools import web_search_tool
from app.execution.tools.cache import clear_cache
from app.execution.tools.web_search_tool import BRAVE_SEARCH_URL, web_search

DUCKDUCKGO_PAGE = (
    '<div class="result__body"><a class="result__a" href="https://fallback.example/">Fallback</a>'
    '<a class="result__url" href="https://fallback.example/">fallback.example</a>'
    '<a class="result__snippet" href="https://fallback.example/">From DuckDuckGo</a></div>'
)


def brave_response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock(status_code=status_code)
    response.json.return_value = payload
    return response


class TestBraveProvider(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)
        patcher = patch.object(settings, "BRAVE_SEARCH_API_KEY", "test-key")
        patcher.start()
        self.addCleanup(patcher.stop)
        post = patch.object(web_search_tool.requests, "post", return_value=MagicMock(
            status_code=200, text=DUCKDUCKGO_PAGE, content=DUCKDUCKGO_PAGE.encode()))
        self.ddg = post.start()
        self.addCleanup(post.stop)

    def search(self, query: str = "notion pricing") -> dict:
        return json.loads(web_search.invoke({"query": query, "max_results": 2}))

    def test_uses_brave_with_the_key_in_a_header_and_cleans_markup(self) -> None:
        payload = {"web": {"results": [
            {"title": "Notion &amp; Pricing", "url": "https://www.notion.com/pricing",
             "description": "Plans for <strong>teams</strong>"},
            {"title": "No scheme", "url": "ftp://notion.com/"},
            {"title": "Second", "url": "https://notion.com/b", "description": "b"},
            {"title": "Over the limit", "url": "https://notion.com/c", "description": "c"},
        ]}}
        with patch.object(web_search_tool.requests, "get", return_value=brave_response(payload)) as get:
            result = self.search()

        self.assertEqual(get.call_args.args[0], BRAVE_SEARCH_URL)
        self.assertEqual(get.call_args.kwargs["headers"]["X-Subscription-Token"], "test-key")
        self.assertEqual(get.call_args.kwargs["params"], {"q": "notion pricing", "count": 2})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["results"], [
            {"rank": 1, "title": "Notion & Pricing", "url": "https://www.notion.com/pricing",
             "snippet": "Plans for teams"},
            {"rank": 2, "title": "Second", "url": "https://notion.com/b", "snippet": "b"},
        ])
        self.ddg.assert_not_called()
        self.assertNotIn("test-key", json.dumps(result))

    def test_falls_back_to_duckduckgo_when_brave_cannot_answer(self) -> None:
        failures = {
            "rate limited": brave_response({}, 429),
            "bad key": brave_response({}, 401),
            "no results": brave_response({"web": {"results": []}}),
            "no web section": brave_response({}),
            "unexpected shape": brave_response({"web": []}),
        }
        for name, response in failures.items():
            with self.subTest(name), patch.object(web_search_tool.requests, "get", return_value=response):
                clear_cache()
                self.ddg.reset_mock()
                result = self.search()
                self.ddg.assert_called_once()
                self.assertEqual(result["data"]["results"][0]["url"], "https://fallback.example/")

    def test_falls_back_when_brave_is_unreachable(self) -> None:
        with patch.object(web_search_tool.requests, "get", side_effect=requests.ConnectionError("down")):
            self.assertEqual(self.search()["data"]["results"][0]["url"], "https://fallback.example/")

    def test_without_a_key_brave_is_never_called(self) -> None:
        with patch.object(settings, "BRAVE_SEARCH_API_KEY", ""), \
                patch.object(web_search_tool.requests, "get") as get:
            self.search()
        get.assert_not_called()
        self.ddg.assert_called_once()

    def test_brave_results_are_cached_like_any_other_search(self) -> None:
        payload = {"web": {"results": [{"title": "A", "url": "https://a.example/", "description": "a"}]}}
        with patch.dict(os.environ, {"AGENTFLOW_TOOL_CACHE_TTL_SECONDS": "60"}),                 patch.object(web_search_tool.requests, "get", return_value=brave_response(payload)) as get:
            self.search()
            self.search()
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
