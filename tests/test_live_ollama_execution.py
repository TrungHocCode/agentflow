import os
import sys
import unittest
import shutil

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import State, Task
from app.execution.graph import build_execution_graph
from app.execution.llm import get_llm


class TestLiveOllamaExecution(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        data_dir = os.path.join(os.getcwd(), "workspace_data")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)

    async def test_ollama_llm_direct_invocation(self):
        """Test direct connection to local Ollama server."""
        try:
            llm = get_llm(model_name="qwen3:0.6b", temperature=0.1)
            response = await llm.ainvoke("Say 'Ollama is online' in 5 words or less.")
            self.assertTrue(len(response.content) > 0)
            print(f"\n[Ollama Test Output]: {response.content}")
        except Exception as e:
            self.fail(f"Ollama connection failed: {str(e)}")

    async def test_live_ollama_graph_execution(self):
        """
        Test End-to-End execution with live local Ollama model in WorkerAgent ReAct loop.
        """
        t1 = Task(
            id=1,
            node="news_crawler",
            status="pending",
            description="Crawl article text from https://news.ycombinator.com"
        )
        t2 = Task(
            id=2,
            node="markdown_report_generator",
            status="pending",
            dependencies=[1],
            description="Generate a markdown summary report based on the crawled news and save it."
        )

        initial_state: State = {
            "messages": ["User: Fetch HackerNews frontpage and generate a summary report."],
            "plan": [t1, t2],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "executing",
            "metadata": {
                "use_llm": True,
                "model_name": "qwen3:0.6b"
            }
        }

        compiled_graph = build_execution_graph()
        final_state = await compiled_graph.ainvoke(initial_state)

        final_plan = final_state.get("plan") or []
        self.assertEqual(len(final_plan), 2)
        self.assertTrue(all(t.status == "done" for t in final_plan))


if __name__ == "__main__":
    unittest.main()
