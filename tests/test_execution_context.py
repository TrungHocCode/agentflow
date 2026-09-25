import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.execution.graph import build_execution_graph
from app.execution.state import State


class TestExecutionContext(unittest.IsolatedAsyncioTestCase):
    async def test_token_callback_is_available_to_supervisor_node_without_state_persistence(self):
        streamed_tokens: list[str] = []

        async def on_token(token: str) -> None:
            streamed_tokens.append(token)

        async def fake_supervisor(state, agent_resolver=None, on_assistant_token=None):
            del state, agent_resolver
            self.assertIsNotNone(on_assistant_token)
            await on_assistant_token("Live ")
            await on_assistant_token("reply")
            return {
                "mode": "conversation",
                "messages": [AIMessage(content="Live reply")],
                "metadata": {"supervisor_decision": "answer"},
            }

        initial_state: State = {
            "messages": [],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {},
        }
        with patch("app.execution.graph.supervisor_node", side_effect=fake_supervisor):
            graph = build_execution_graph(
                checkpointer=MemorySaver(),
                agent_resolver=object(),
            )
            result = await graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": "token-context-test"}},
                context={"on_assistant_token": on_token},
            )

        self.assertEqual(streamed_tokens, ["Live ", "reply"])
        self.assertEqual(result["messages"][-1].content, "Live reply")
        self.assertNotIn("on_assistant_token", result.get("metadata", {}))


if __name__ == "__main__":
    unittest.main()
