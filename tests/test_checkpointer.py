"""
Tests cho LangGraph Checkpointer integration và HITL (Human-in-the-Loop) flow.

Kiểm tra:
- get_checkpointer() trả về singleton MemorySaver
- reset_checkpointer() reset đúng cách (dùng trong tests)
- Graph compile thành công với checkpointer
- Graph PAUSE đúng sau supervisor_node (mode=conversation → interrupt)
- Graph RESUME thành công sau khi inject mode=executing
- Multi-thread isolation: 2 run_id khác nhau không ảnh hưởng lẫn nhau
- get_graph_config() trả về đúng cấu trúc thread_id
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from test_support import isolated_workspace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from langgraph.checkpoint.memory import MemorySaver

from app.execution.checkpointer import get_checkpointer, reset_checkpointer
from app.execution.graph import build_execution_graph, get_graph_config
from app.execution.state import State, Task


class TestCheckpointerFactory(unittest.TestCase):
    """Tests cho singleton checkpointer factory."""

    def setUp(self):
        reset_checkpointer()

    def tearDown(self):
        reset_checkpointer()

    def test_get_checkpointer_returns_memory_saver(self):
        """get_checkpointer() phải trả về MemorySaver instance."""
        cp = get_checkpointer()
        self.assertIsInstance(cp, MemorySaver)

    def test_get_checkpointer_is_singleton(self):
        """Hai lần gọi get_checkpointer() phải trả về cùng 1 object."""
        cp1 = get_checkpointer()
        cp2 = get_checkpointer()
        self.assertIs(cp1, cp2)

    def test_reset_checkpointer_creates_new_instance(self):
        """reset_checkpointer() phải tạo instance mới ở lần gọi tiếp theo."""
        cp1 = get_checkpointer()
        reset_checkpointer()
        cp2 = get_checkpointer()
        self.assertIsNot(cp1, cp2)

    def test_get_graph_config_structure(self):
        """get_graph_config() phải trả về dict đúng cấu trúc configurable/thread_id."""
        run_id = "test-run-abc-123"
        config = get_graph_config(run_id)
        self.assertIn("configurable", config)
        self.assertIn("thread_id", config["configurable"])
        self.assertEqual(config["configurable"]["thread_id"], run_id)

    def test_get_graph_config_unique_per_run(self):
        """Mỗi run_id phải tạo config với thread_id khác nhau."""
        config1 = get_graph_config("run-1")
        config2 = get_graph_config("run-2")
        self.assertNotEqual(
            config1["configurable"]["thread_id"],
            config2["configurable"]["thread_id"]
        )


class TestGraphWithCheckpointer(unittest.IsolatedAsyncioTestCase):
    """Tests cho graph execution với checkpointer tích hợp."""

    def setUp(self):
        reset_checkpointer()
        self._test_checkpointer = MemorySaver()
        self.enterContext(isolated_workspace())
        response = MagicMock(status_code=200)
        response.text = (
            "<html><head><title>Fixture</title></head><body><article><p>"
            "Deterministic source content for checkpoint isolation, with enough evidence to avoid browser rendering."
            "</p></article></body></html>"
        )
        self.enterContext(patch("requests.get", return_value=response))

    def tearDown(self):
        reset_checkpointer()

    def _build_graph(self):
        """Build graph với MemorySaver riêng để isolation trong tests."""
        return build_execution_graph(checkpointer=self._test_checkpointer)

    async def test_graph_compiles_with_checkpointer(self):
        """Graph phải compile thành công khi truyền checkpointer."""
        graph = self._build_graph()
        self.assertIsNotNone(graph)

    async def test_graph_pauses_in_conversation_mode(self):
        """
        Graph phải PAUSE (interrupt) sau supervisor_node khi mode='conversation'
        và route trả về 'dispatcher_node'.
        Kết quả trả về phải là state trung gian, không phải END.
        """
        graph = self._build_graph()
        run_id = "test-pause-001"
        config = get_graph_config(run_id)

        initial_state: State = {
            "messages": ["Tôi muốn crawl tin tức về AI"],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {}
        }

        # Lần gọi đầu: graph chạy supervisor_node → đề xuất plan → PAUSE trước dispatcher_node
        result_state = await graph.ainvoke(initial_state, config=config)

        # Phải có plan được đề xuất
        plan = result_state.get("plan") or []
        self.assertGreater(len(plan), 0, "Supervisor phải đề xuất ít nhất 1 task")

        # Mode vẫn là conversation (chưa approve)
        mode = result_state.get("mode")
        self.assertEqual(mode, "conversation", "Mode phải là 'conversation' sau khi supervisor đề xuất plan")

        # HITLGate đã chạy — phải có log từ hitl_gate
        logs = result_state.get("logs") or []
        self.assertTrue(
            any("HITLGate" in str(l) for l in logs),
            "Phải có log từ HITLGate — xác nhận graph đã đi qua checkpoint node"
        )

        # Không có task nào đã chạy (dispatcher chưa được gọi)
        for task in plan:
            self.assertEqual(task.status, "pending", f"Task {task.id} phải là 'pending' — dispatcher chưa chạy")

    async def test_graph_resumes_after_approve(self):
        """
        Test HITL 2-turn flow:
        - Turn 1: mode=conversation → supervisor đề xuất plan → hitl_gate → END (PAUSE).
        - Turn 2: ainvoke với {mode: executing} → supervisor nhận executing
                  → dispatcher → workers → END (toàn bộ plan done).
        """
        from unittest.mock import patch, MagicMock

        graph = self._build_graph()
        run_id = "test-resume-001"
        config = get_graph_config(run_id)

        # --- Turn 1: Supervisor đề xuất plan (mode=conversation) ---
        initial_state: State = {
            "messages": ["Tôi muốn crawl tin tức và tạo báo cáo"],
            "plan": [],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {}
        }
        state_after_turn_1 = await graph.ainvoke(initial_state, config=config)

        # Turn 1: supervisor đề xuất plan, mode phải là conversation, hitl_gate đã chạy
        self.assertEqual(state_after_turn_1.get("mode"), "conversation",
                         "Mode phải là conversation sau Turn 1")
        plan_turn_1 = state_after_turn_1.get("plan") or []
        self.assertGreater(len(plan_turn_1), 0, "Supervisor phải đề xuất ít nhất 1 task")
        logs_turn_1 = state_after_turn_1.get("logs") or []
        self.assertTrue(
            any("HITLGate" in str(l) for l in logs_turn_1),
            "HITLGate phải đã chạy trong Turn 1"
        )
        # Tasks vẫn pending — dispatcher chưa chạy
        for task in plan_turn_1:
            self.assertEqual(task.status, "pending",
                             f"Task {task.id} phải là pending sau Turn 1")

        # --- Turn 2: Resume với mode=executing (user đã approve) ---
        # Supervisor nhận mode=executing → route→dispatcher → workers → END
        with patch("requests.get") as mock_get:
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.text = "<html><body><p>AI news test content.</p></body></html>"
            mock_get.return_value = mock_res

            final_state = await graph.ainvoke({"mode": "executing"}, config=config)

        final_plan = final_state.get("plan") or []
        self.assertGreater(len(final_plan), 0, "Plan phải có task sau khi resume")

        # Tất cả task phải hoàn thành
        for task in final_plan:
            self.assertIn(
                task.status, ("done", "failed", "skipped"),
                f"Task {task.id} phải kết thúc sau khi execute, got: {task.status}"
            )

    async def test_multi_thread_isolation(self):
        """
        Hai run song song với thread_id khác nhau không được ảnh hưởng lẫn nhau.
        State của run-A không được rò rỉ vào run-B.
        """
        graph = self._build_graph()
        run_id_a = "isolation-run-A"
        run_id_b = "isolation-run-B"
        config_a = get_graph_config(run_id_a)
        config_b = get_graph_config(run_id_b)

        # Tạo plan khác nhau cho 2 run
        task_a = Task(id=1, node="news_crawler", status="pending", description="Crawl for run A")
        task_b = Task(id=1, node="text_summarizer", status="pending", description="Summarize for run B")

        state_a: State = {
            "messages": ["run A message"],
            "plan": [task_a],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {"run": "A"}
        }
        state_b: State = {
            "messages": ["run B message"],
            "plan": [task_b],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "conversation",
            "metadata": {"run": "B"}
        }

        result_a = await graph.ainvoke(state_a, config=config_a)
        result_b = await graph.ainvoke(state_b, config=config_b)

        # Metadata của A không được xuất hiện trong B
        meta_a = result_a.get("metadata") or {}
        meta_b = result_b.get("metadata") or {}
        self.assertNotEqual(meta_a.get("run"), meta_b.get("run"))


if __name__ == "__main__":
    unittest.main()
