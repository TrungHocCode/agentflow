"""
Test multi-turn conversation → execution flow với LangGraph Checkpointer.

Sau khi tích hợp checkpointer, flow thay đổi:
- Turn 1 (conversation): supervisor đề xuất plan, graph PAUSE tại END (mode=conversation không trigger interrupt).
- Turn 2 (approve): supervisor nhận approval keyword → mode=executing → route→dispatcher → INTERRUPT trước dispatcher.
- Turn 3 (resume): inject {"mode": "executing"} → resume dispatcher → workers → END.

Ngoài ra test cũng kiểm tra mỗi turn phải dùng cùng thread_id để graph
tiếp tục từ checkpoint đúng chỗ.
"""
import os
import sys
import uuid
import unittest
from test_support import isolated_workspace
from unittest.mock import patch, MagicMock

# Adjust path to import backend app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.execution.state import State, Task
from app.execution.graph import build_execution_graph, get_graph_config
from app.execution.checkpointer import reset_checkpointer


class TestConversationalSupervisor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(isolated_workspace())
        reset_checkpointer()
        # Dùng MemorySaver riêng mỗi test để isolation
        self._checkpointer = MemorySaver()

    def tearDown(self):
        reset_checkpointer()

    async def test_multi_turn_conversation_to_execution_flow(self):
        """
        Test multi-turn interaction với HITL checkpoint flow:
        Turn 1: User gửi yêu cầu → Supervisor đề xuất plan, mode='conversation',
                graph đi qua hitl_gate rồi END (dừng lại chờ user).
        Turn 2: Resume với mode='executing' → graph tiếp tục từ checkpoint,
                supervisor nhận mode=executing → dispatcher → workers → END.
        """
        compiled_graph = build_execution_graph(checkpointer=self._checkpointer)
        run_id = str(uuid.uuid4())
        config = get_graph_config(run_id)

        # --- Turn 1: Initial conversation ---
        state_turn_1: State = {
            "messages": ["User: Cào tin tức từ https://news.ycombinator.com và tạo báo cáo cho tôi."],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {}
        }
        output_turn_1 = await compiled_graph.ainvoke(state_turn_1, config=config)

        # Turn 1 assertions
        self.assertEqual(output_turn_1.get("mode"), "conversation")
        proposed_plan = output_turn_1.get("plan") or []
        self.assertGreater(len(proposed_plan), 0, "Supervisor phải đề xuất ít nhất 1 task")
        self.assertEqual(len(output_turn_1.get("result_storage") or []), 0,
                         "Workers chưa được chạy sau Turn 1")
        # HITLGate phải đã được thực thi
        logs_turn_1 = output_turn_1.get("logs") or []
        self.assertTrue(any("HITLGate" in str(l) for l in logs_turn_1),
                        "HITLGate node phải đã chạy trong Turn 1")

        # --- Turn 2: User approve — resume graph với mode='executing' ---
        with patch("requests.get") as mock_get:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.text = "<html><body><p>AI and LangGraph news test content.</p></body></html>"
            mock_get.return_value = mock_res

            # Truyền None → resume từ checkpoint đã lưu
            output_turn_2 = await compiled_graph.ainvoke(
                {"mode": "executing"},
                config=config
            )

        # Turn 2 assertions: tất cả tasks phải hoàn thành
        final_plan = output_turn_2.get("plan") or []
        self.assertGreater(len(final_plan), 0)
        for task in final_plan:
            self.assertIn(task.status, ("done", "failed", "skipped"),
                          f"Task {task.id} phải hoàn thành, got: {task.status}")

        # Phải có kết quả từ workers
        results = output_turn_2.get("result_storage") or []
        self.assertGreater(len(results), 0, "Phải có ít nhất 1 kết quả từ workers")


if __name__ == "__main__":
    unittest.main()
