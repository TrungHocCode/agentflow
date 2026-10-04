"""Context safety regression tests; no network, model or persistent development data."""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app.core.config import settings, Settings
from app.execution.agents.base import WorkerAgent
from app.execution.context_budget import ContextBudgetExceeded, guard_context, project_tool_result
from app.execution.state import Task
from app.execution.tools.contracts import success_result


class ResearchContextTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_context_settings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Settings(_env_file=None, LLM_CONTEXT_TOKENS=2048, LLM_OUTPUT_TOKENS=2048)
        with self.assertRaises(ValueError):
            Settings(_env_file=None, RESEARCH_MAX_CHUNKS=0)
    def test_large_duplicate_search_is_bounded(self) -> None:
        candidates = [{"title": "Benchmark", "url": f"https://example.org/{i}", "snippet": "x" * 3000}
                      for i in range(25)]
        raw = success_result({"results": candidates, "queries": [{"results": candidates}]})
        projected = project_tool_result(raw)
        self.assertLess(len(projected), 5000)
        self.assertIsInstance(json.loads(projected), dict)
        self.assertNotIn('"queries"', projected)

    def test_compaction_keeps_latest_tool_pair(self) -> None:
        def request(identity: str) -> AIMessage:
            return AIMessage(content="", tool_calls=[{"id": identity, "name": "search", "args": {}}])
        messages = [SystemMessage(content="Keep constraints"), HumanMessage(content="Keep task"),
                    request("old"), ToolMessage(content="x" * 10000, tool_call_id="old"),
                    request("new"), ToolMessage(content="Evidence", tool_call_id="new")]
        with patch.object(settings, "LLM_CONTEXT_TOKENS", 4096):
            guard_context(messages, [])
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[-1].tool_call_id, "new")
        self.assertEqual(messages[0].content, "Keep constraints")

    def test_required_input_is_not_silently_cut(self) -> None:
        messages = [SystemMessage(content="x" * 50000)]
        with self.assertRaises(ContextBudgetExceeded):
            guard_context(messages, [])
        self.assertEqual(len(messages[0].content), 50000)

    async def test_batch_result_does_not_overflow_next_worker_call(self) -> None:
        @tool
        def search() -> str:
            """Return a deliberately oversized mocked search response."""
            return success_result({"results": [{"url": f"https://example.org/{i}", "snippet": "x" * 3000}
                                               for i in range(25)]}).to_json()

        llm = MagicMock()
        calls = AsyncMock(side_effect=[AIMessage(content="", tool_calls=[
            {"name": "search", "args": {}, "id": "call1"}]), AIMessage(content="Discovery only")])
        llm.bind_tools.return_value.ainvoke = calls
        worker = WorkerAgent("worker", "Do research", llm, [search])
        output = await worker.execute({"current_task": Task(id=1, node="worker", status="pending",
                                                              description="Find sources"), "messages": []})
        self.assertEqual(output["current_task"].status, "done")
        self.assertEqual(calls.await_count, 2)

    async def test_only_dependency_results_are_passed(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="Done"))
        worker = WorkerAgent("worker", "Synthesize", llm)
        await worker.execute({"current_task": Task(id=3, node="worker", status="pending",
                                                    description="Compare", dependencies=[1]),
                              "result_storage": [{"task_id": 1, "result": "Relevant evidence"},
                                                 {"task_id": 2, "result": "Unrelated text"}]})
        prompt = llm.ainvoke.call_args.args[0][0].content
        self.assertIn("Relevant evidence", prompt)
        self.assertNotIn("Unrelated text", prompt)
