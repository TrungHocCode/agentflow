import os
import sys
import uuid
import unittest
import shutil
from unittest.mock import patch, MagicMock

# Adjust path to import backend app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import State, Task
from app.execution.graph import build_execution_graph, get_graph_config


class TestGraphExecution(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        data_dir = os.path.join(os.getcwd(), "workspace_data")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)

    @patch("requests.get")
    async def test_end_to_end_graph_execution(self, mock_get):
        """
        Test End-to-End LangGraph execution loop:
        Supervisor -> Dispatcher -> Worker (news_crawler -> text_summarizer -> markdown_report_generator) -> END
        """
        # Mock HTML response for news crawler tool
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.text = """
        <html>
            <head><title>HackerNews Today</title></head>
            <body>
                <p>AI breakthrough in autonomous agentic coding frameworks.</p>
                <p>LangGraph and FastAPI enable deterministic multi-agent flows.</p>
            </body>
        </html>
        """
        mock_get.return_value = mock_res

        # 1. Define execution plan tasks
        t1 = Task(id=1, node="news_crawler", status="pending", description="Crawl news from https://news.ycombinator.com")
        t2 = Task(id=2, node="text_summarizer", status="pending", dependencies=[1], description="Summarize crawled news text into key bullet points")
        t3 = Task(id=3, node="markdown_report_generator", status="pending", dependencies=[2], description="Generate Markdown report")

        initial_state: State = {
            "messages": ["User: Crawl HackerNews, summarize it, and generate a markdown report."],
            "plan": [t1, t2, t3],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "executing",
            "metadata": {}
        }

        # 2. Build and invoke graph — phải truyền config với thread_id khi dùng checkpointer
        compiled_graph = build_execution_graph()
        run_id = str(uuid.uuid4())
        config = get_graph_config(run_id)
        final_state = await compiled_graph.ainvoke(initial_state, config=config)

        # 3. Assertions
        final_plan = final_state.get("plan") or []
        self.assertEqual(len(final_plan), 3)
        self.assertTrue(all(t.status == "done" for t in final_plan))

        results = final_state.get("result_storage") or []
        self.assertEqual(len(results), 3)

        # Verify output file generated
        report_file = os.path.join(os.getcwd(), "workspace_data", "reports", "intelligence_report.md")
        self.assertTrue(os.path.exists(report_file))


if __name__ == "__main__":
    unittest.main()
