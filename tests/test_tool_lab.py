import asyncio
import json
import os
import shutil
import sys
import unittest
from unittest.mock import MagicMock, patch

import requests


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

import app.execution.tools.registry  # noqa: F401
from app.execution.tools.contracts import is_tool_failure, parse_tool_result
from app.execution.tools.base import ToolRegistry
from app.execution.tools.cache import clear_cache
from app.execution.tools.tool_runner import run_tool
from app.execution.nodes.dispatcher import TaskDispatcher
from app.execution.state import Task


class TestToolContracts(unittest.TestCase):
    def test_legacy_success_is_normalized(self):
        result = parse_tool_result("legacy output", tool_name="demo")

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")
        self.assertTrue(result.metadata.model_extra["legacy"])

    def test_legacy_error_is_normalized_as_failure(self):
        result = parse_tool_result("Error: upstream unavailable", tool_name="demo")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "internal_error")
        self.assertTrue(is_tool_failure(result))
        self.assertEqual(result.error.code, "legacy_tool_error")

    def test_structured_json_is_parsed(self):
        payload = {
            "ok": True,
            "status": "success",
            "data": {"value": 42},
            "metadata": {"tool_name": "demo"},
        }

        result = parse_tool_result(json.dumps(payload), tool_name="demo")

        self.assertEqual(result.data["value"], 42)
        self.assertEqual(result.metadata.tool_name, "demo")


class TestToolLab(unittest.TestCase):
    def tearDown(self):
        clear_cache()
        workspace_data = os.path.join(os.getcwd(), "workspace_data")
        if os.path.exists(workspace_data):
            shutil.rmtree(workspace_data)

    def test_failed_tool_skips_the_full_dependent_chain(self):
        plan = [
            Task(id=1, node="news_crawler", status="failed", description="crawl"),
            Task(id=2, node="text_summarizer", status="pending", dependencies=[1], description="summarize"),
            Task(id=3, node="report", status="pending", dependencies=[2], description="report"),
        ]

        result = asyncio.run(TaskDispatcher().dispatch({"plan": plan}))

        self.assertEqual([(task.id, task.status) for task in result["plan"]], [(2, "skipped"), (3, "skipped")])

    @patch("app.execution.tools.news_crawler_tool.requests.get")
    def test_runner_returns_structured_article_result(self, mock_get):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/article"
        response.headers = {"content-type": "text/html; charset=utf-8"}
        response.content = b"<html>"
        response.text = """
        <html>
          <head><title>Research Article</title></head>
          <body>
            <article>
              <p>This is a sufficiently long paragraph describing a research result and its impact on modern software systems.</p>
              <p>The second paragraph adds evidence and context so that the extractor can classify this page as an article.</p>
            </article>
          </body>
        </html>
        """
        mock_get.return_value = response

        result = run_tool("news_crawler", {"url": "https://example.com/article"})

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["content_type"], "article")
        self.assertGreater(result.metadata.duration_ms, -1)
        self.assertEqual(result.source.status_code, 200)

    @patch("app.execution.tools.news_crawler_tool.requests.get")
    def test_empty_page_does_not_look_like_success(self, mock_get):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/empty"
        response.headers = {"content-type": "text/html"}
        response.content = b"<html></html>"
        response.text = "<html><head><title>Empty</title></head><body></body></html>"
        mock_get.return_value = response

        result = run_tool("news_crawler", {"url": "https://example.com/empty"})

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "empty")
        self.assertTrue(is_tool_failure(result))

    @patch("app.execution.tools.news_crawler_tool.requests.get")
    def test_listing_page_returns_items_instead_of_fake_article(self, mock_get):
        links = "".join(
            f'<a href="https://example.com/item-{index}">Story {index} about engineering</a>'
            for index in range(12)
        )
        navigation = (
            '<a href="https://news.ycombinator.com/item?id=42">42 comments</a>'
            '<a href="https://news.ycombinator.com/login">Login</a>'
        )
        response = MagicMock()
        response.status_code = 200
        response.url = "https://news.ycombinator.com/"
        response.headers = {"content-type": "text/html"}
        response.content = f"{links}{navigation}".encode("utf-8")
        response.text = (
            f"<html><head><title>Hacker News</title></head>"
            f"<body>{links}{navigation}</body></html>"
        )
        mock_get.return_value = response

        result = run_tool(
            "news_crawler",
            {"url": "https://news.ycombinator.com/", "max_articles": 0},
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["content_type"], "listing")
        self.assertEqual(len(result.data["items"]), 12)
        self.assertEqual([item["rank"] for item in result.data["items"]], list(range(1, 13)))

    @patch(
        "app.execution.tools.news_crawler_tool.validate_external_url",
        side_effect=lambda url: (url, None),
    )
    @patch("app.execution.tools.news_crawler_tool.crawl_url")
    def test_listing_crawls_only_bounded_number_of_linked_articles(
        self,
        mock_crawl,
        _mock_validate,
    ):
        listing_url = "https://news.ycombinator.com/"
        article_urls = [
            "https://example.com/story-1",
            "https://example.com/story-2",
            "https://example.com/story-3",
        ]
        article_text = (
            "The research team published a new benchmark for local language models. "
            "The benchmark measures latency, accuracy, and memory use across several model sizes."
        )

        def fake_crawl(url, mode, *, extract_page):
            if url == listing_url:
                return json.dumps({
                    "ok": True,
                    "status": "success",
                    "data": {
                        "content_type": "listing",
                        "title": "Hacker News",
                        "text": "Homepage navigation and story titles.",
                        "items": [
                            {"rank": index, "title": f"Story {index}", "url": article_url}
                            for index, article_url in enumerate(article_urls, 1)
                        ],
                        "metadata": {},
                    },
                    "source": {
                        "requested_url": listing_url,
                        "final_url": listing_url,
                        "status_code": 200,
                        "content_type": "text/html",
                    },
                    "metadata": {"tool_name": "news_crawler", "duration_ms": 4},
                })
            return json.dumps({
                "ok": True,
                "status": "success",
                "data": {
                    "content_type": "article",
                    "title": f"Fetched {url}",
                    "text": article_text,
                    "metadata": {"author": "Research team"},
                },
                "source": {
                    "requested_url": url,
                    "final_url": url,
                    "status_code": 200,
                    "content_type": "text/html",
                },
                "metadata": {"tool_name": "news_crawler", "duration_ms": 9, "warnings": []},
            })

        mock_crawl.side_effect = fake_crawl
        result = run_tool(
            "news_crawler",
            {"url": listing_url, "max_articles": 2},
        )

        self.assertTrue(result.ok, result.model_dump_json())
        self.assertEqual(result.data["article_count"], 2)
        self.assertEqual(len(result.data["articles"]), 2)
        self.assertEqual(
            [article["requested_url"] for article in result.data["articles"]],
            article_urls[:2],
        )
        self.assertEqual(mock_crawl.call_count, 3)  # Listing plus two articles, never recursive.

    @patch(
        "app.execution.tools.news_crawler_tool.validate_external_url",
        side_effect=lambda url: (url, None),
    )
    @patch("app.execution.tools.news_crawler_tool.crawl_url")
    def test_listing_fails_if_no_linked_article_has_usable_body(self, mock_crawl, _mock_validate):
        listing_url = "https://example.com/news"
        listing_result = json.dumps({
            "ok": True,
            "status": "success",
            "data": {
                "content_type": "listing",
                "title": "News",
                "text": "Only list-page snippets.",
                "items": [{"rank": 1, "title": "A news story", "url": "https://example.com/story"}],
            },
            "source": {"requested_url": listing_url, "final_url": listing_url},
            "metadata": {"tool_name": "news_crawler"},
        })
        not_article_result = json.dumps({
            "ok": True,
            "status": "success",
            "data": {"content_type": "listing", "text": "A second list, not the article.", "items": []},
            "metadata": {"tool_name": "news_crawler"},
        })
        mock_crawl.side_effect = [listing_result, not_article_result]

        result = run_tool("news_crawler", {"url": listing_url, "max_articles": 1})

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "no_articles_extracted")
        self.assertEqual(result.data["articles"][0]["status"], "not_article")

    def test_summarizer_prefers_crawled_article_text_over_listing_text(self):
        crawler_result = {
            "ok": True,
            "status": "success",
            "data": {
                "content_type": "listing",
                "text": "Homepage navigation should not be summarized.",
                "articles": [{
                    "title": "Local model benchmark",
                    "requested_url": "https://example.com/benchmark",
                    "final_url": "https://example.com/benchmark",
                    "status": "success",
                    "text": (
                        "The research team published a new benchmark for local language models. "
                        "The benchmark measures latency, accuracy, and memory use across several model sizes."
                    ),
                }],
            },
            "source": {"final_url": "https://news.ycombinator.com/"},
        }

        result = parse_tool_result(
            ToolRegistry.get_tool("text_summarizer").invoke({
                "text": json.dumps(crawler_result),
                "max_bullet_points": 20,
            })
        )

        self.assertTrue(result.ok)
        self.assertIn("published a new benchmark", result.data["summary"])
        self.assertNotIn("Homepage navigation", result.data["summary"])
        self.assertIn("https://example.com/benchmark", result.data["sources"])

    def test_unknown_tool_is_a_normalized_failure(self):
        result = run_tool("does_not_exist", {})

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "invalid_input")
        self.assertEqual(result.error.code, "unknown_tool")

    @patch("app.execution.tools.web_search_tool.requests.post")
    def test_web_search_returns_ranked_citations(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.text = """
        <div class="result">
          <a class="result__a" href="https://example.com/article">Article title</a>
          <a class="result__url" href="https://example.com/article">example.com/article</a>
          <a class="result__snippet">Article evidence snippet</a>
        </div>
        """
        mock_post.return_value = response

        result = run_tool("web_search", {"query": "agent architecture"})

        self.assertTrue(result.ok)
        self.assertEqual(result.data["results"][0]["rank"], 1)
        self.assertEqual(result.data["results"][0]["url"], "https://example.com/article")

    def test_summarizer_rejects_empty_crawler_evidence(self):
        crawler_failure = {
            "ok": False,
            "status": "empty",
            "data": {"text": ""},
            "error": {"code": "no_content", "message": "No content", "retryable": False},
        }

        result = parse_tool_result(
            ToolRegistry.get_tool("text_summarizer").invoke({"text": json.dumps(crawler_failure)})
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.error.code, "insufficient_evidence")

    def test_chart_escapes_svg_and_accepts_negative_values(self):
        result = parse_tool_result(
            ToolRegistry.get_tool("chart_generator").invoke({
                "title": "Revenue <2026>",
                "labels": ["Q1 & Q2", "Q3"],
                "values": [-10, 20],
                "filename": "safe_chart",
            })
        )

        self.assertTrue(result.ok)
        with open(result.data["svg_path"], encoding="utf-8") as stream:
            svg = stream.read()
        self.assertIn("&lt;2026&gt;", svg)
        self.assertNotIn("<2026>", svg)

    def test_http_request_blocks_localhost(self):
        result = parse_tool_result(
            ToolRegistry.get_tool("http_request").invoke({"url": "http://127.0.0.1:8000/health"})
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error.code, "outbound_url_blocked")

    def test_report_sanitizes_filename_and_keeps_source_provenance(self):
        result = parse_tool_result(
            ToolRegistry.get_tool("markdown_report_generator").invoke({
                "title": "Evidence report",
                "summary": "Summary",
                "sections": [{
                    "header": "Source",
                    "content": "Evidence from https://example.com/research.",
                }],
                "filename": "../unsafe name",
            })
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["file_path"].endswith("unsafe_name.md"))
        self.assertEqual(result.data["source_urls"], ["https://example.com/research"])

    def test_report_collects_source_from_structured_tool_envelope(self):
        evidence = json.dumps({
            "ok": True,
            "status": "success",
            "data": {"text": "Collected evidence."},
            "source": {
                "requested_url": "https://example.com/requested",
                "final_url": "https://example.com/final",
            },
        })
        result = parse_tool_result(
            ToolRegistry.get_tool("markdown_report_generator").invoke({
                "title": "Structured evidence",
                "sections": [{"header": "Evidence", "content": evidence}],
                "filename": "structured_evidence.md",
            })
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.data["source_urls"],
            ["https://example.com/requested", "https://example.com/final"],
        )

    @patch.dict(os.environ, {"AGENTFLOW_TOOL_CACHE_TTL_SECONDS": "60"})
    @patch("app.execution.tools.news_crawler_tool.requests.get")
    def test_crawler_cache_avoids_duplicate_fetches(self, mock_get):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/cached"
        response.headers = {"content-type": "text/html"}
        response.content = b"<html>"
        response.text = "<html><body><p>This is enough article content to satisfy the crawler quality threshold for caching.</p></body></html>"
        mock_get.return_value = response

        first = run_tool("news_crawler", {"url": "https://example.com/cached"})
        second = run_tool("news_crawler", {"url": "https://example.com/cached"})

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        mock_get.assert_called_once()

    @patch("app.execution.tools.network_policy.time.sleep")
    @patch("app.execution.tools.web_search_tool.requests.post")
    def test_search_retries_transient_connection_failure(self, mock_post, mock_sleep):
        response = MagicMock()
        response.status_code = 200
        response.text = '<div class="result"><a class="result__a" href="https://example.com">Result</a><a class="result__url" href="https://example.com">example.com</a></div>'
        mock_post.side_effect = [requests.ConnectionError("temporary"), response]

        result = run_tool("web_search", {"query": "retry test"})

        self.assertTrue(result.ok)
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
