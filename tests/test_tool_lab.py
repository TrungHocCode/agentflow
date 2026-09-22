import asyncio
import json
import os
import shutil
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

import app.execution.tools.registry  # noqa: F401
from app.execution.tools.contracts import is_tool_failure, parse_tool_result
from app.execution.tools.base import ToolRegistry
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
        response = MagicMock()
        response.status_code = 200
        response.url = "https://news.ycombinator.com/"
        response.headers = {"content-type": "text/html"}
        response.content = links.encode("utf-8")
        response.text = f"<html><head><title>Hacker News</title></head><body>{links}</body></html>"
        mock_get.return_value = response

        result = run_tool("news_crawler", {"url": "https://news.ycombinator.com/"})

        self.assertTrue(result.ok)
        self.assertEqual(result.data["content_type"], "listing")
        self.assertEqual(len(result.data["items"]), 12)

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


if __name__ == "__main__":
    unittest.main()
