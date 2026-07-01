import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Adjust path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from app.execution.state import State, Task, SupervisorOutput
from app.execution.agents.base import SupervisorAgent, WorkerAgent
from app.execution.agents.registry import AgentRegistry


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
            mode="executing",
            assistant_message="Creating a plan.",
            plan=[
                Task(id=1, node="worker", status="pending", error=None, description="Run first task")
            ],
            direction="academic"
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
            "direction": None,
            "pending_review_content": None,
            "review_status": None
        }

        updates = await supervisor.execute(state)

        # Assertions
        self.mock_llm.with_structured_output.assert_called_once_with(SupervisorOutput)
        mock_structured_llm.ainvoke.assert_called_once()
        
        self.assertEqual(updates["mode"], "executing")
        self.assertEqual(updates["direction"], "academic")
        self.assertEqual(len(updates["messages"]), 1)
        self.assertEqual(updates["messages"][0].content, "Creating a plan.")
        self.assertEqual(len(updates["plan"]), 1)
        self.assertEqual(updates["plan"][0].description, "Run first task")

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
            "direction": None,
            "pending_review_content": None,
            "review_status": None
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
            "direction": None,
            "pending_review_content": None,
            "review_status": None
        }

        updates = await worker.execute(state)

        # Assertions
        self.mock_llm.bind_tools.assert_called_once_with([add])
        self.assertEqual(mock_llm_with_tools.ainvoke.call_count, 2)
        
        self.assertEqual(updates["current_task"].status, "done")
        self.assertEqual(updates["plan"][0].status, "done")
        self.assertEqual(updates["result_storage"][0]["result"], "The result is 5.")
        self.assertTrue(any("Executing tool 'add' with args {'a': 2, 'b': 3}" in log for log in updates["logs"]))


if __name__ == "__main__":
    unittest.main()
