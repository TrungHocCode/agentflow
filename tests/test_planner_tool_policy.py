"""Planning decides business tasks; deterministic backend policy decides tool permissions."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langchain_core.language_models import BaseChatModel
from app.core.config import settings
from app.execution.agents.base import SupervisorAgent
from app.execution.agents.resolver import AgentResolver
from app.execution.state import PlannedTask, SupervisorOutput, Task
from app.execution.tools.registry import autodiscover_tools


class TestPlannerToolPolicy(unittest.IsolatedAsyncioTestCase):
    def test_planner_schema_excludes_execution_permissions(self) -> None:
        fields = set(PlannedTask.model_json_schema()["properties"])
        self.assertEqual(fields, {
            "id", "node", "status", "description", "dependencies", "expected_output_type", "research_requirements"
        })

    async def test_generated_tool_selection_cannot_restrict_or_expand_role_permissions(self) -> None:
        autodiscover_tools()
        response = SupervisorOutput.model_validate({
            "decision": "propose_plan",
            "assistant_message": "Research first, then summarize. Please approve.",
            "plan": [
                {"id": 1, "node": "source_researcher", "description": "Find and read primary sources",
                 "tool_names": ["web_search", "python_executor"], "tool_ids": ["invented-id"],
                 "max_iterations": 999, "config": {"tool_names": ["python_executor"]}},
                {"id": 2, "node": "synthesis_agent", "description": "Synthesize evidence",
                 "dependencies": [1], "expected_output_type": "summary"},
            ],
        })
        llm = MagicMock(spec=BaseChatModel)
        structured = AsyncMock()
        structured.ainvoke.return_value = response
        llm.with_structured_output.return_value = structured
        supervisor = SupervisorAgent(name="supervisor", system_prompt="Plan research.", llm=llm)
        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", False):
            updates = await supervisor.execute({"messages": [], "plan": [], "metadata": {}})
        task = updates["plan"][0]
        self.assertIsInstance(task, Task)
        self.assertEqual(task.tool_names, [])
        self.assertEqual(task.tool_ids, [])
        self.assertIsNone(task.max_iterations)
        self.assertEqual(task.config, {})
        self.assertEqual(updates["plan"][1].dependencies, [1])
        resolver = AgentResolver()
        await resolver.validate_plan(updates["plan"])
        tools = {tool.name for tool in (await resolver.resolve(task)).tools}
        self.assertTrue({"web_search", "web_search_batch", "news_crawler", "news_crawler_batch",
                         "http_request"} <= tools)
        self.assertNotIn("python_executor", tools)

    async def test_user_authored_tool_restriction_remains_enforced(self) -> None:
        autodiscover_tools()
        resolver = AgentResolver()
        task = Task(id=1, node="source_researcher", status="pending", description="Search only",
                    tool_names=["web_search"])
        self.assertEqual([tool.name for tool in (await resolver.resolve(task)).tools], ["web_search"])
        task.tool_names = ["file_writer"]
        with self.assertRaises(ValueError):
            await resolver.validate_plan([task])
