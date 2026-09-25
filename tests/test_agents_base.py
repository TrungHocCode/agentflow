import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Adjust path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from app.core.config import settings
from app.execution.state import State, Task, SupervisorOutput
from app.execution.agents.base import SupervisorAgent, WorkerAgent
from app.execution.agents.registry import AgentRegistry
from app.execution.tools.contracts import success_result


class TestAgentPlatformBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_llm = MagicMock(spec=BaseChatModel)

    def test_agent_registry(self):
        """Test registry registers and instantiates agents correctly."""
        # Check pre-registered agents
        self.assertIn("supervisor", AgentRegistry.list_agents())
        self.assertIn("worker", AgentRegistry.list_agents())
        
        self.assertEqual(AgentRegistry.get_agent_class("supervisor"), SupervisorAgent)
        self.assertEqual(AgentRegistry.get_agent_class("worker"), WorkerAgent)

        # Test dynamic custom registration
        @AgentRegistry.register("custom_agent")
        class CustomAgent(WorkerAgent):
            pass

        self.assertIn("custom_agent", AgentRegistry.list_agents())
        self.assertEqual(AgentRegistry.get_agent_class("custom_agent"), CustomAgent)

    async def test_supervisor_agent_execution(self):
        """Test SupervisorAgent executes and parses state updates correctly."""
        supervisor = SupervisorAgent(
            name="Supervisor",
            system_prompt="You are a supervisor.",
            llm=self.mock_llm
        )

        mock_structured_llm = AsyncMock()
        self.mock_llm.with_structured_output.return_value = mock_structured_llm

        # Mock structured LLM response
        mock_output = SupervisorOutput(
            decision="propose_plan",
            mode="executing",
            assistant_message="Creating a plan.",
            plan=[
                Task(id=1, node="worker", status="pending", error=None, description="Run first task")
            ],
            metadata={"direction": "academic"}
        )
        mock_structured_llm.ainvoke.return_value = mock_output

        # Input state
        state: State = {
            "messages": ["Hello supervisor"],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {}
        }

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", False):
            updates = await supervisor.execute(state)

        # Assertions
        self.mock_llm.with_structured_output.assert_called_once_with(SupervisorOutput)
        mock_structured_llm.ainvoke.assert_called_once()
        
        self.assertEqual(updates["mode"], "conversation")
        self.assertIn("explicit user approval", updates["logs"][0])
        self.assertEqual(
            updates["metadata"],
            {
                "direction": "academic",
                "supervisor_decision": "propose_plan",
            },
        )
        self.assertEqual(len(updates["messages"]), 1)
        self.assertEqual(updates["messages"][0].content, "Creating a plan.")
        self.assertEqual(len(updates["plan"]), 1)
        self.assertEqual(updates["plan"][0].description, "Run first task")
        self.assertNotIn("execution_timings", updates)

    async def test_supervisor_cannot_override_internal_inference_purpose(self):
        supervisor = SupervisorAgent(
            name="Supervisor",
            system_prompt="You are a supervisor.",
            llm=self.mock_llm,
        )
        structured_llm = AsyncMock()
        self.mock_llm.with_structured_output.return_value = structured_llm
        structured_llm.ainvoke.return_value = SupervisorOutput(
            decision="answer",
            assistant_message="SSE keeps a response stream open.",
            metadata={"inference_purpose": "worker"},
        )
        state: State = {
            "messages": ["SSE là gì?"],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {"inference_purpose": "chat"},
        }

        updates = await supervisor.execute(state)

        self.assertEqual(updates["metadata"]["inference_purpose"], "chat")

    async def test_supervisor_timing_uses_routed_model_when_recording_metrics(self):
        supervisor = SupervisorAgent(
            name="Supervisor",
            system_prompt="You are a supervisor.",
            llm=self.mock_llm,
        )
        structured_llm = AsyncMock()
        self.mock_llm.with_structured_output.return_value = structured_llm
        structured_llm.ainvoke.return_value = SupervisorOutput(
            decision="answer",
            assistant_message="SSE keeps a response stream open.",
        )
        state: State = {
            "messages": ["SSE là gì?"],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {"inference_purpose": "chat"},
        }

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            with patch.object(settings, "LLM_CHAT_MODEL", "chat-test-model"):
                with patch(
                    "app.execution.agents.base.perf_counter",
                    side_effect=[1.0, 1.125],
                ):
                    updates = await supervisor.execute(state)

        self.assertEqual(updates["execution_timings"][0]["model"], "chat-test-model")

    async def test_supervisor_structured_output_failure_does_not_fallback_to_plain_chat(self):
        supervisor = SupervisorAgent(
            name="Supervisor",
            system_prompt="You are a supervisor.",
            llm=self.mock_llm,
        )
        structured_llm = AsyncMock()
        structured_llm.ainvoke.side_effect = RuntimeError("invalid structured response")
        self.mock_llm.with_structured_output.return_value = structured_llm
        self.mock_llm.ainvoke = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "invalid structured response"):
            await supervisor.execute(
                {
                    "messages": [HumanMessage(content="Research a topic")],
                    "plan": [],
                    "mode": "conversation",
                    "metadata": {},
                }
            )

        self.mock_llm.ainvoke.assert_not_awaited()

    async def test_supervisor_only_enters_execution_after_user_approval(self) -> None:
        supervisor = SupervisorAgent(
            name="Supervisor",
            system_prompt="You are a supervisor.",
            llm=self.mock_llm,
        )
        existing_plan = [
            Task(id=1, node="source_researcher", status="pending", description="Find sources")
        ]

        updates = await supervisor.execute(
            {
                "messages": [HumanMessage(content="đồng ý, chạy đi")],
                "plan": existing_plan,
                "current_task": None,
                "logs": [],
                "result_storage": [],
                "mode": "conversation",
                "metadata": {"use_llm": True},
            }
        )

        self.assertEqual(updates["mode"], "executing")
        self.mock_llm.with_structured_output.assert_not_called()

    async def test_worker_agent_execution_no_tools(self):
        """Test WorkerAgent executing without tools."""
        worker = WorkerAgent(
            name="Coder",
            system_prompt="You write python code.",
            llm=self.mock_llm
        )

        # Mock standard LLM return value
        self.mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="def hello(): pass"))

        current_task = Task(id=1, node="Coder", status="running", error=None, description="Write hello function")
        state: State = {
            "messages": [],
            "plan": [current_task],
            "current_task": current_task,
            "logs": [],
            "result_storage": [],
            "mode": "executing",
            "metadata": {}
        }

        updates = await worker.execute(state)

        # Assert ainvoke called
        self.mock_llm.ainvoke.assert_called_once()

        # Assert state updates
        self.assertEqual(updates["current_task"].status, "done")
        self.assertEqual(updates["plan"][0].status, "done")
        self.assertEqual(len(updates["result_storage"]), 1)
        self.assertEqual(updates["result_storage"][0]["result"], "def hello(): pass")
        self.assertEqual(updates["result_storage"][0]["status"], "done")

    async def test_worker_agent_includes_run_input_context(self):
        worker = WorkerAgent(
            name="Researcher",
            system_prompt="You research sources.",
            llm=self.mock_llm,
        )
        self.mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="Done"))
        current_task = Task(
            id=1,
            node="worker",
            status="running",
            description="Crawl the requested article",
        )

        await worker.execute(
            {
                "messages": [],
                "plan": [current_task],
                "current_task": current_task,
                "logs": [],
                "result_storage": [],
                "mode": "executing",
                "metadata": {
                    "input_data": {
                        "user_prompt": "Summarize https://news.example.com/article",
                    }
                },
            }
        )

        system_message = self.mock_llm.ainvoke.call_args.args[0][0]
        self.assertIn("https://news.example.com/article", system_message.content)
        self.assertIn("do not invent placeholders", system_message.content.lower())

    async def test_worker_agent_execution_with_tools_loop(self):
        """Test WorkerAgent executing with a tool loop (ReAct)."""
        @tool
        def add(a: int, b: int) -> int:
            """Adds two integers."""
            return a + b

        worker = WorkerAgent(
            name="Calculator",
            system_prompt="You solve math problems.",
            llm=self.mock_llm,
            tools=[add]
        )

        # Mock LLM tool binding
        mock_llm_with_tools = AsyncMock()
        self.mock_llm.bind_tools.return_value = mock_llm_with_tools

        # Setup consecutive answers: first requests tool, second returns final message
        tool_call_response = AIMessage(
            content="",
            tool_calls=[{"name": "add", "args": {"a": 2, "b": 3}, "id": "call_123"}]
        )
        final_response = AIMessage(content="The result is 5.")
        mock_llm_with_tools.ainvoke.side_effect = [tool_call_response, final_response]

        current_task = Task(id=2, node="Calculator", status="running", error=None, description="Add 2 and 3")
        state: State = {
            "messages": [],
            "plan": [current_task],
            "current_task": current_task,
            "logs": [],
            "result_storage": [],
            "mode": "executing",
            "metadata": {}
        }

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            with patch(
                "app.execution.agents.base.perf_counter",
                side_effect=[10.0, 10.25, 20.0, 20.2, 30.0, 30.5],
            ):
                updates = await worker.execute(state)

        # Assertions
        self.mock_llm.bind_tools.assert_called_once_with([add])
        self.assertEqual(mock_llm_with_tools.ainvoke.call_count, 2)
        
        self.assertEqual(updates["current_task"].status, "done")
        self.assertEqual(updates["plan"][0].status, "done")
        self.assertEqual(updates["result_storage"][0]["result"], "The result is 5.")
        self.assertTrue(any("Executing tool 'add' with args {'a': 2, 'b': 3}" in log for log in updates["logs"]))
        timings = updates["execution_timings"]
        self.assertEqual([timing["operation"] for timing in timings], ["llm", "tool", "llm"])
        self.assertEqual([timing["duration_ms"] for timing in timings], [250.0, 200.0, 500.0])
        self.assertEqual([timing["iteration"] for timing in timings], [1, 1, 2])
        self.assertEqual(timings[1]["call_id"], "call_123")

    async def test_worker_agent_marks_final_tool_error_as_failed(self):
        @tool
        def broken_tool() -> str:
            """Returns a deterministic tool failure for testing."""
            return "Error: upstream service unavailable"

        worker = WorkerAgent(
            name="Researcher",
            system_prompt="You research sources.",
            llm=self.mock_llm,
            tools=[broken_tool],
        )
        mock_llm_with_tools = AsyncMock()
        self.mock_llm.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.ainvoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[{"name": "broken_tool", "args": {}, "id": "call-error"}],
            ),
            AIMessage(content="I could not fetch the source."),
        ]
        current_task = Task(
            id=1,
            node="worker",
            status="running",
            description="Fetch source",
        )

        updates = await worker.execute(
            {
                "messages": [],
                "plan": [current_task],
                "current_task": current_task,
                "logs": [],
                "result_storage": [],
                "mode": "executing",
                "metadata": {},
            }
        )

        self.assertEqual(updates["current_task"].status, "failed")
        self.assertIn("upstream service unavailable", updates["current_task"].error)

    async def test_worker_agent_keeps_successful_sources_when_another_source_fails(self):
        @tool
        def search_source(query: str) -> str:
            """Returns one independently successful or failed search result."""
            if query == "missing source":
                return "Error: search provider returned no results"
            return f"Verified evidence for {query}"

        worker = WorkerAgent(
            name="Researcher",
            system_prompt="Search for evidence and report gaps.",
            llm=self.mock_llm,
            tools=[search_source],
        )
        mock_llm_with_tools = AsyncMock()
        self.mock_llm.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.ainvoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_source", "args": {"query": "Claude"}, "id": "call-claude"},
                    {"name": "search_source", "args": {"query": "missing source"}, "id": "call-missing"},
                ],
            ),
            AIMessage(content="Claude was verified from an available source."),
        ]
        current_task = Task(
            id=1,
            node="source_researcher",
            status="running",
            description="Research several model sources",
        )

        updates = await worker.execute(
            {
                "messages": [],
                "plan": [current_task],
                "current_task": current_task,
                "logs": [],
                "result_storage": [],
                "mode": "executing",
                "metadata": {},
            }
        )

        self.assertEqual(updates["current_task"].status, "partial")
        self.assertEqual(updates["result_storage"][0]["status"], "partial")
        self.assertIn("missing source", updates["result_storage"][0]["result"])
        self.assertIn("no results", updates["current_task"].error)

    async def test_worker_agent_fails_when_usable_source_coverage_is_below_half(self):
        @tool
        def search_source(query: str) -> str:
            """Returns one successful source among three requested sources."""
            return "Verified evidence" if query == "available" else "Error: no results"

        worker = WorkerAgent(
            name="Researcher",
            system_prompt="Search for evidence and report gaps.",
            llm=self.mock_llm,
            tools=[search_source],
        )
        mock_llm_with_tools = AsyncMock()
        self.mock_llm.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.ainvoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_source", "args": {"query": "available"}, "id": "call-available"},
                    {"name": "search_source", "args": {"query": "missing one"}, "id": "call-missing-one"},
                    {"name": "search_source", "args": {"query": "missing two"}, "id": "call-missing-two"},
                ],
            ),
            AIMessage(content="A complete report based on this limited result."),
        ]
        current_task = Task(
            id=1,
            node="source_researcher",
            status="running",
            description="Research three distinct sources",
        )

        updates = await worker.execute(
            {
                "messages": [],
                "plan": [current_task],
                "current_task": current_task,
                "logs": [],
                "result_storage": [],
                "mode": "executing",
                "metadata": {},
            }
        )

        self.assertEqual(updates["current_task"].status, "failed")
        self.assertIn("1 of 3", updates["current_task"].error)
        self.assertIn("at least 2", updates["current_task"].error)

    async def test_worker_agent_applies_coverage_threshold_inside_partial_batch(self):
        @tool
        def search_batch(queries: list[str]) -> str:
            """Returns per-query search coverage for a batch."""
            return success_result(
                {
                    "queries": [
                        {"query": "one", "status": "success", "result_count": 2},
                        {"query": "two", "status": "success", "result_count": 1},
                        {
                            "query": "three",
                            "status": "empty",
                            "result_count": 0,
                            "error": {"code": "no_results", "message": "No results."},
                        },
                    ],
                    "results": [],
                },
                tool_name="web_search_batch",
                status="partial",
            ).to_json()

        worker = WorkerAgent(
            name="Researcher",
            system_prompt="Search multiple sources and report coverage.",
            llm=self.mock_llm,
            tools=[search_batch],
        )
        mock_llm_with_tools = AsyncMock()
        self.mock_llm.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.ainvoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "search_batch",
                    "args": {"queries": ["one", "two", "three"]},
                    "id": "call-batch",
                }],
            ),
            AIMessage(content="Two of three query angles returned evidence."),
        ]
        current_task = Task(
            id=1,
            node="source_researcher",
            status="running",
            description="Research multiple query angles",
        )

        updates = await worker.execute(
            {
                "messages": [],
                "plan": [current_task],
                "current_task": current_task,
                "logs": [],
                "result_storage": [],
                "mode": "executing",
                "metadata": {},
            }
        )

        self.assertEqual(updates["current_task"].status, "partial")
        self.assertIn("2/3 usable", updates["current_task"].error)

    async def test_worker_agent_does_not_mark_recovered_retry_as_partial(self):
        calls = {"count": 0}

        @tool
        def search_source(query: str) -> str:
            """Returns a temporary failure once, then a successful retry."""
            calls["count"] += 1
            return "Verified evidence" if calls["count"] == 2 else "Error: temporary provider failure"
        worker = WorkerAgent(
            name="Researcher",
            system_prompt="Retry a transient search failure.",
            llm=self.mock_llm,
            tools=[search_source],
        )
        mock_llm_with_tools = AsyncMock()
        self.mock_llm.bind_tools.return_value = mock_llm_with_tools
        mock_llm_with_tools.ainvoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "search_source", "args": {"query": "same"}, "id": "call-1"}]),
            AIMessage(content="", tool_calls=[{"name": "search_source", "args": {"query": "same"}, "id": "call-2"}]),
            AIMessage(content="The search succeeded after retry."),
        ]
        current_task = Task(id=1, node="source_researcher", status="running", description="Search one source")

        updates = await worker.execute(
            {
                "messages": [],
                "plan": [current_task],
                "current_task": current_task,
                "logs": [],
                "result_storage": [],
                "mode": "executing",
                "metadata": {},
            }
        )

        self.assertEqual(updates["current_task"].status, "done")



if __name__ == "__main__":
    unittest.main()
