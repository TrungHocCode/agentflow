import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.model_router import InferencePurpose, model_name_for, route_conversation


class TestModelRouting(unittest.TestCase):
    def test_simple_question_uses_chat_profile(self) -> None:
        self.assertEqual(route_conversation("SSE là gì?"), InferencePurpose.CHAT)

    def test_direct_small_talk_uses_chat_profile(self) -> None:
        self.assertEqual(route_conversation("Cảm ơn bạn"), InferencePurpose.CHAT)

    def test_research_request_uses_planner_profile(self) -> None:
        self.assertEqual(
            route_conversation("Research recent local LLM benchmarks"),
            InferencePurpose.PLANNER,
        )

    def test_url_request_uses_planner_profile(self) -> None:
        self.assertEqual(
            route_conversation("Tóm tắt trang https://example.org/research"),
            InferencePurpose.PLANNER,
        )

    def test_pending_plan_and_clarification_always_use_planner(self) -> None:
        self.assertEqual(
            route_conversation("SSE là gì?", has_pending_plan=True),
            InferencePurpose.PLANNER,
        )
        self.assertEqual(
            route_conversation("SSE là gì?", previous_decision="clarify"),
            InferencePurpose.PLANNER,
        )

    def test_follow_up_after_direct_answer_uses_chat_unless_requesting_research(self) -> None:
        self.assertEqual(
            route_conversation("Vậy tại sao cần SSE?", previous_decision="answer"),
            InferencePurpose.CHAT,
        )
        self.assertEqual(
            route_conversation("Research SSE implementations", previous_decision="answer"),
            InferencePurpose.PLANNER,
        )

    def test_uncertain_message_defaults_to_planner(self) -> None:
        self.assertEqual(
            route_conversation("Hãy giúp tôi xử lý việc này."),
            InferencePurpose.PLANNER,
        )

    def test_long_explanation_request_uses_planner_profile(self) -> None:
        request = "Giải thích " + ("kiến trúc multi-agent và các trade-off " * 12)
        self.assertEqual(route_conversation(request), InferencePurpose.PLANNER)

    def test_each_purpose_uses_its_configured_model(self) -> None:
        with patch("app.execution.model_router.settings") as mocked_settings:
            mocked_settings.LLM_CHAT_MODEL = "chat-model"
            mocked_settings.LLM_PLANNER_MODEL = "planner-model"
            mocked_settings.LLM_WORKER_MODEL = "worker-model"

            self.assertEqual(model_name_for(InferencePurpose.CHAT), "chat-model")
            self.assertEqual(model_name_for(InferencePurpose.PLANNER), "planner-model")
            self.assertEqual(model_name_for(InferencePurpose.WORKER), "worker-model")

    def test_unknown_purpose_defaults_to_planner_model(self) -> None:
        with patch("app.execution.model_router.settings") as mocked_settings:
            mocked_settings.LLM_PLANNER_MODEL = "planner-model"
            self.assertEqual(model_name_for("unexpected"), "planner-model")


if __name__ == "__main__":
    unittest.main()
