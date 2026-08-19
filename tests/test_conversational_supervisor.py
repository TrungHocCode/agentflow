import os
import sys
import unittest
import shutil

# Adjust path to import backend app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import State, Task
from app.execution.graph import build_execution_graph


class TestConversationalSupervisor(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        data_dir = os.path.join(os.getcwd(), "workspace_data")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)

    async def test_multi_turn_conversation_to_execution_flow(self):
        """
        Test multi-turn interaction:
        Turn 1: User sends initial request -> Supervisor proposes plan, mode stays 'conversation', graph halts at END.
        Turn 2: User approves plan ('đồng ý') -> Supervisor switches mode to 'executing', graph proceeds through Workers -> END.
        """
        compiled_graph = build_execution_graph()

        # Turn 1: Initial conversation
        state_turn_1: State = {
            "messages": ["User: Cào tin tức từ https://news.ycombinator.com và tạo báo cáo cho tôi."],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {}
        }

        output_turn_1 = await compiled_graph.ainvoke(state_turn_1)

        # Assert Turn 1: Mode is conversation, plan proposed, workers have NOT executed yet
        self.assertEqual(output_turn_1.get("mode"), "conversation")
        proposed_plan = output_turn_1.get("plan") or []
        self.assertTrue(len(proposed_plan) > 0)
        self.assertEqual(len(output_turn_1.get("result_storage") or []), 0)

        from langchain_core.messages import HumanMessage, AIMessage

        # Turn 2: User responds with approval
        state_turn_2: State = {
            "messages": [
                HumanMessage(content="Cào tin tức từ https://news.ycombinator.com và tạo báo cáo cho tôi."),
                AIMessage(content="Supervisor: Tôi đã lập xong kế hoạch 3 bước. Bạn có đồng ý thực thi không?"),
                HumanMessage(content="Đồng ý, chạy đi!")
            ],
            "plan": proposed_plan,
            "current_task": None,
            "logs": output_turn_1.get("logs") or [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {}
        }

        output_turn_2 = await compiled_graph.ainvoke(state_turn_2)

        # Assert Turn 2: All tasks executed to completion
        final_plan = output_turn_2.get("plan") or []
        self.assertEqual(len(final_plan), 3)
        self.assertTrue(all(t.status == "done" for t in final_plan))
        self.assertEqual(len(output_turn_2.get("result_storage") or []), 3)

        # Verify logs confirm transition to executing and completion
        logs = output_turn_2.get("logs") or []
        self.assertTrue(any("Transitioning to 'executing'" in str(l) for l in logs))
        self.assertTrue(any("All tasks in plan finished execution" in str(l) for l in logs))





if __name__ == "__main__":
    unittest.main()
