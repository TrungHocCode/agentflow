import os
import sys
import unittest
from test_support import isolated_workspace
from typing import Any
from unittest.mock import patch, MagicMock

# Adjust path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

# Importing registry triggers autodiscovery automatically
import app.execution.tools.registry
from app.execution.state import Task, add_messages, add_results, update_plan
from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import failure_result, parse_tool_result, success_result
from app.execution.tools.news_crawler_tool import normalize_news_url


class TestToolsAndReducers(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(isolated_workspace())

    def test_state_reducers(self):
        """Test the state reducers perform list merging correctly."""
        # 1. Test add_messages keeps last 10
        left_msgs = [f"Msg {i}" for i in range(8)]
        right_msgs = ["New Msg 1", "New Msg 2", "New Msg 3"]
        merged_msgs = add_messages(left_msgs, right_msgs)
        self.assertEqual(len(merged_msgs), 10)
        self.assertEqual(merged_msgs[0], "Msg 1")
        self.assertEqual(merged_msgs[-1], "New Msg 3")

        # 2. Test add_results merges correctly
        results_left = [{"task_id": 1, "result": "Done"}]
        results_right = [{"task_id": 2, "result": "Running"}]
        self.assertEqual(len(add_results(results_left, results_right)), 2)

        # 3. Test update_plan merges tasks by ID
        t1 = Task(id=1, node="A", status="pending", error=None, description="Task A")
        t2 = Task(id=2, node="B", status="pending", error=None, description="Task B")
        t1_updated = Task(id=1, node="A", status="done", error=None, description="Task A Updated")
        
        plan = [t1, t2]
        updated_plan = update_plan(plan, [t1_updated])
        
        self.assertEqual(len(updated_plan), 2)
        self.assertEqual(updated_plan[0].status, "done")
        self.assertEqual(updated_plan[0].description, "Task A Updated")
        self.assertEqual(updated_plan[1].status, "pending")

    def test_tool_registry_autodiscovery(self):
        """Test ToolRegistry list and retrieval works via autodiscovery."""
        tools = ToolRegistry.list_tools()
        self.assertIn("python_executor", tools)
        self.assertIn("web_search", tools)
        self.assertIn("web_search_batch", tools)
        self.assertIn("news_crawler_batch", tools)
        self.assertIn("file_writer", tools)
        self.assertIn("file_reader", tools)
        self.assertIn("database_query", tools)
        self.assertIn("http_request", tools)
        self.assertIn("email_sender", tools)

        self.assertEqual(ToolRegistry.get_tool("python_executor").name, "python_executor")

    def test_python_executor(self):
        """Test PythonExecutor executes code correctly in subprocess."""
        executor = ToolRegistry.get_tool("python_executor")
        code = "print(2 + 3)"
        output = executor.invoke({"code": code})
        self.assertEqual(output.strip(), "5")

        code_error = "import sys; sys.exit(1)"
        output_error = executor.invoke({"code": code_error})
        self.assertTrue("Exit code 1" in output_error)

    @patch("requests.post")
    def test_web_search(self, mock_post):
        """Test WebSearch returns mock search results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <div class="result__body">
            <a class="result__snippet" href="http://example.com/item1">Snippet 1 details</a>
            <a class="result__url" href="http://example.com/item1">example.com/item1</a>
        </div>
        """
        mock_post.return_value = mock_response

        search_tool = ToolRegistry.get_tool("web_search")
        output = search_tool.invoke({"query": "testing"})
        self.assertTrue("Snippet 1" in output)
        self.assertTrue("example.com/item1" in output)

    def test_database_query_tool(self):
        """Test database query tool executes SQL on the SQLite sandbox."""
        db_tool = ToolRegistry.get_tool("database_query")
        
        # Test query users table
        res = db_tool.invoke({"query": "SELECT name, role FROM users WHERE id = 1"})
        self.assertTrue("Alice" in res)
        self.assertTrue("admin" in res)

    @patch("requests.request")
    def test_http_request_tool(self, mock_request):
        """Test HTTP request tool executes and outputs clean response results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "Success"}
        mock_response.text = '{"message": "Success"}'
        mock_request.return_value = mock_response

        http_tool = ToolRegistry.get_tool("http_request")
        res = http_tool.invoke({
            "url": "https://api.example.com",
            "method": "POST",
            "data": '{"test": true}'
        })
        self.assertTrue("HTTP Status: 200" in res)
        self.assertTrue("Success" in res)

    def test_email_sender_tool(self):
        """Test mock email sender tool executes successfully."""
        email_tool = ToolRegistry.get_tool("email_sender")
        res = email_tool.invoke({
            "recipient": "bob@example.com",
            "subject": "Platform test",
            "body": "Hello world"
        })
        self.assertTrue("Successfully sent" in res)
        self.assertTrue("bob@example.com" in res)

    def test_file_writer_and_reader(self):
        """Test file writing and reading within workspace."""
        writer = ToolRegistry.get_tool("file_writer")
        reader = ToolRegistry.get_tool("file_reader")
        filename = "test_run.txt"
        content = "Testing AgentFlow File Tools."
        
        # Write file
        write_res = writer.invoke({"filename": filename, "content": content})
        self.assertTrue("Successfully wrote" in write_res)

        # Verify it was saved inside workspace_data
        self.assertTrue(os.path.exists(os.path.join(os.getcwd(), "workspace_data", filename)))

        # Read file
        read_res = reader.invoke({"filename": filename})
        self.assertEqual(read_res, content)
        
        # Read missing file
        missing_res = reader.invoke({"filename": "missing.txt"})
        self.assertTrue("Error:" in missing_res)

    @patch("requests.get")
    def test_news_crawler_tool(self, mock_get):
        """Test news crawler tool fetches and parses article paragraphs."""
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = """
        <html>
            <head><title>AI Breakthrough News</title></head>
            <body>
                <p>Paragraph 1: AI Agents are revolutionizing autonomous software engineering workflows worldwide.</p>
                <p>Paragraph 2: Researchers announce state of the art results on agent benchmarks.</p>
            </body>
        </html>
        """
        mock_get.return_value = mock_res

        crawler = ToolRegistry.get_tool("news_crawler")
        res = crawler.invoke({"url": "https://news.example.com/ai-breakthrough"})
        self.assertTrue("AI Breakthrough News" in res)
        self.assertTrue("Paragraph 1:" in res)

    def test_news_crawler_normalizes_markdown_url(self):
        normalized, error = normalize_news_url(
            "[https://news.example.com/article](https://news.example.com/article)"
        )
        self.assertEqual(normalized, "https://news.example.com/article")
        self.assertIsNone(error)

        normalized, error = normalize_news_url("example.com/article")
        self.assertIsNone(normalized)
        self.assertIn("HTTP(S)", error)

    def test_text_summarizer_tool(self):
        """Test text summarizer tool extracts bullet points."""
        summarizer = ToolRegistry.get_tool("text_summarizer")
        sample_text = (
            "First sentence about artificial intelligence and modern agentic coding platforms. "
            "Second sentence describing how LangGraph nodes coordinate deterministic worker agents. "
            "Third sentence explaining the role of tool registries and prompt injection defense."
        )
        res = summarizer.invoke({"text": sample_text, "max_bullet_points": 2})
        self.assertTrue("TEXT SUMMARY" in res)
        self.assertTrue("- First sentence" in res)

    def test_markdown_report_generator_tool(self):
        """Test markdown report generator tool writes formatted report into workspace_data."""
        report_gen = ToolRegistry.get_tool("markdown_report_generator")
        res = report_gen.invoke({
            "title": "Weekly Tech Intelligence Report",
            "summary": "Key market trends and agent architecture updates.",
            "sections": [
                {"header": "Background", "content": "Overview of stateful agent systems."},
                {"header": "Key Takeaways", "content": "1. Multi-tier memory architecture.\n2. Modular tools."}
            ],
            "filename": "tech_report.md"
        })
        self.assertTrue("Successfully generated Markdown report" in res)
        report_file = os.path.join(os.getcwd(), "workspace_data", "reports", "tech_report.md")
        self.assertTrue(os.path.exists(report_file))


class FakeSearchTool:
    def invoke(self, arguments: dict[str, Any]) -> str:
        query = arguments["query"]
        if query == "failed angle":
            return failure_result(
                "timeout",
                code="search_timeout",
                message="Provider timed out.",
                tool_name="web_search",
            ).to_json()
        url = "https://example.com/shared#top" if "overview" in query else "https://example.com/shared"
        return success_result(
            {
                "query": query,
                "results": [{"rank": 1, "title": query, "url": url, "snippet": "Evidence snippet"}],
            },
            tool_name="web_search",
        ).to_json()


class FakeCrawlerTool:
    def invoke(self, arguments: dict[str, Any]) -> str:
        url = arguments["url"]
        if "unavailable" in url:
            return failure_result(
                "http_error",
                code="upstream_error",
                message="Source unavailable.",
                tool_name="news_crawler",
            ).to_json()
        return success_result(
            {
                "content_type": "article",
                "title": "Research article",
                "text": "A sufficiently detailed source body for downstream evidence synthesis.",
                "metadata": {"author": "Example Author"},
            },
            tool_name="news_crawler",
        ).to_json()


class TestBatchResearchTools(unittest.TestCase):
    def test_search_batch_deduplicates_citations_and_keeps_partial_query_errors(self):
        batch_tool = ToolRegistry.get_tool("web_search_batch")
        with patch.object(ToolRegistry, "get_tool", return_value=FakeSearchTool()):
            raw_result = batch_tool.invoke(
                {
                    "queries": ["overview of technology", "implementation details", "failed angle"],
                    "max_results_per_query": 4,
                    "concurrency": 2,
                }
            )

        result = parse_tool_result(raw_result, tool_name="web_search_batch")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.data["deduplicated_count"], 1)
        self.assertEqual(len(result.data["results"]), 1)
        self.assertEqual(
            result.data["results"][0]["matched_queries"],
            ["overview of technology", "implementation details"],
        )
        self.assertEqual(result.data["queries"][2]["status"], "timeout")
        self.assertEqual(result.metadata.model_extra["max_concurrency"], 2)

    def test_crawl_batch_preserves_source_results_and_isolates_failures(self):
        batch_tool = ToolRegistry.get_tool("news_crawler_batch")
        with (
            patch.object(ToolRegistry, "get_tool", return_value=FakeCrawlerTool()),
            patch(
                "app.execution.tools.news_crawler_batch_tool.validate_external_url",
                side_effect=lambda url: (url, None),
            ),
        ):
            raw_result = batch_tool.invoke(
                {
                    "urls": [
                        "https://example.com/article",
                        "https://example.com/unavailable",
                        "https://example.com/article#section",
                    ],
                    "concurrency": 2,
                }
            )

        result = parse_tool_result(raw_result, tool_name="news_crawler_batch")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.metadata.model_extra["unique_url_count"], 2)
        self.assertEqual(len(result.data["sources"]), 2)
        self.assertTrue(result.data["sources"][0]["ok"])
        self.assertEqual(
            result.data["sources"][0]["data"]["text"],
            "A sufficiently detailed source body for downstream evidence synthesis.",
        )
        self.assertFalse(result.data["sources"][1]["ok"])
        self.assertEqual(result.data["sources"][1]["error"]["code"], "upstream_error")

    def test_crawl_batch_rejects_private_targets_before_invoking_crawler(self):
        batch_tool = ToolRegistry.get_tool("news_crawler_batch")
        with patch(
            "app.execution.tools.news_crawler_batch_tool.validate_external_url",
            return_value=(None, "Private and local network targets are blocked."),
        ), patch.object(ToolRegistry, "get_tool") as get_tool:
            raw_result = batch_tool.invoke(
                {"urls": ["http://127.0.0.1:8000/internal"]}
            )

        result = parse_tool_result(raw_result, tool_name="news_crawler_batch")
        self.assertFalse(result.ok)
        self.assertEqual(result.data["sources"][0]["status"], "blocked")
        self.assertEqual(result.data["sources"][0]["error"]["code"], "outbound_url_blocked")
        get_tool.assert_not_called()

    def test_crawl_batch_bounds_redundant_article_content_for_local_model_context(self):
        from app.execution.tools.news_crawler_batch_tool import MAX_SOURCE_TEXT_CHARS, _source_record

        result = success_result(
            {
                "content_type": "article",
                "text": "x" * (MAX_SOURCE_TEXT_CHARS + 100),
                "markdown": "duplicate representation",
                "paragraphs": ["duplicate representation"],
            },
            tool_name="news_crawler",
        )

        source = _source_record("https://example.com/article", result)
        self.assertEqual(len(source["data"]["text"]), MAX_SOURCE_TEXT_CHARS)
        self.assertTrue(source["data"]["text_truncated"])
        self.assertEqual(source["data"]["original_text_length"], MAX_SOURCE_TEXT_CHARS + 100)
        self.assertNotIn("markdown", source["data"])
        self.assertNotIn("paragraphs", source["data"])


if __name__ == "__main__":
    unittest.main()
