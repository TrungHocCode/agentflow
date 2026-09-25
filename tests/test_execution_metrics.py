import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.runs.models import RunDocument, RunResponse
from app.modules.conversations.models import ConversationResponse
from app.modules.runs.service import RunService
from app.core.config import settings
from app.shared.execution_metrics import (
    ExecutionTiming,
    merge_execution_timings,
    summarize_execution_timings,
)


class TestExecutionMetrics(unittest.TestCase):
    def _timing(
        self,
        *,
        operation: str,
        name: str,
        duration_ms: float,
        phase: str = "execute",
        status: str = "success",
        result_status: str | None = None,
        span_id: str | None = None,
    ) -> ExecutionTiming:
        now = datetime.now(timezone.utc)
        return ExecutionTiming(
            span_id=span_id or f"{operation}-{name}-{duration_ms}",
            operation=operation,
            phase=phase,
            name=name,
            agent_name=name if operation == "llm" else "source_researcher",
            task_id=1,
            duration_ms=duration_ms,
            status=status,
            result_status=result_status,
            started_at=now,
            completed_at=now,
        )

    def test_summary_groups_llm_and_tools_and_tracks_phases(self):
        spans = [
            self._timing(operation="llm", name="source_researcher", duration_ms=1200),
            self._timing(
                operation="tool",
                name="web_search",
                duration_ms=300,
                result_status="success",
            ),
            self._timing(
                operation="tool",
                name="news_crawler",
                duration_ms=500,
                result_status="partial",
            ),
            self._timing(
                operation="llm",
                name="supervisor",
                duration_ms=700,
                phase="plan",
            ),
        ]

        summary = summarize_execution_timings(spans)

        self.assertEqual(summary["llm"]["call_count"], 2)
        self.assertEqual(summary["llm"]["total_ms"], 1900)
        self.assertEqual(summary["tools"]["call_count"], 2)
        self.assertEqual(summary["tools"]["total_ms"], 800)
        self.assertEqual(summary["tools"]["partial_count"], 1)
        self.assertEqual(summary["tools"]["by_tool"]["news_crawler"]["total_ms"], 500)
        self.assertEqual(summary["phase_totals_ms"]["plan"]["llm_ms"], 700)
        self.assertEqual(summary["phase_totals_ms"]["execute"]["total_ms"], 2000)

    def test_merge_deduplicates_resumed_state_by_span_id(self):
        span = self._timing(
            operation="llm",
            name="supervisor",
            duration_ms=75,
            phase="plan",
            span_id="stable-span",
        )

        merged = merge_execution_timings([span], [span])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["span_id"], "stable-span")

    def test_run_service_persists_internal_timing_spans_without_emitting_events(self):
        span = self._timing(
            operation="tool",
            name="web_search",
            duration_ms=321.5,
            result_status="success",
        )
        run = RunDocument(run_id="run-1", flow_id="flow-1", status="running")
        service = object.__new__(RunService)

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            events = service._apply_execution_output(
                run,
                "worker_node",
                {"execution_timings": [span]},
            )

        self.assertEqual(run.metadata["execution_metrics"]["tools"]["total_ms"], 321.5)
        self.assertEqual(run.metadata["execution_timings"][0]["name"], "web_search")
        self.assertEqual(events, [])

    def test_run_service_does_not_collect_metrics_when_disabled(self):
        span = self._timing(operation="tool", name="web_search", duration_ms=321.5)
        run = RunDocument(run_id="run-1", flow_id="flow-1", status="running")
        service = object.__new__(RunService)

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", False):
            events = service._apply_execution_output(
                run,
                "worker_node",
                {"execution_timings": [span]},
            )

        self.assertNotIn("execution_timings", run.metadata)
        self.assertNotIn("execution_metrics", run.metadata)
        self.assertEqual(events, [])

    def test_public_conversation_response_hides_internal_benchmark_metrics(self):
        now = datetime.now(timezone.utc)
        response = ConversationResponse(
            id="conversation-1",
            user_id="user-1",
            status="active",
            created_at=now,
            updated_at=now,
            metadata={
                "execution_timings": [{"duration_ms": 20}],
                "execution_metrics": {"total_call_time_ms": 20},
                "chat_ttft_samples": [{"turn_id": "turn-1", "ttft_ms": 42.0}],
                "supervisor_decision": "answer",
            },
        )

        self.assertEqual(response.metadata, {"supervisor_decision": "answer"})

    def test_public_run_response_hides_internal_benchmark_metrics(self):
        now = datetime.now(timezone.utc)
        response = RunResponse(
            run_id="run-1",
            flow_id="flow-1",
            user_id="user-1",
            status="running",
            mode="executing",
            created_at=now,
            updated_at=now,
            metadata={
                "execution_timings": [{"duration_ms": 20}],
                "execution_metrics": {"total_call_time_ms": 20},
                "partial_completion": True,
            },
        )

        self.assertEqual(response.metadata, {"partial_completion": True})

    def test_finalize_adds_wall_clock_and_unattributed_time(self):
        run = RunDocument(
            run_id="run-1",
            flow_id="flow-1",
            execution_time_ms=1800.25,
            metadata={
                "execution_metrics": {
                    "phase_totals_ms": {
                        "execute": {"total_ms": 1250.0},
                    },
                },
            },
        )

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            RunService._finalize_execution_metrics(run)

        self.assertEqual(run.metadata["execution_metrics"]["execute_wall_ms"], 1800.25)
        self.assertEqual(run.metadata["execution_metrics"]["unattributed_execute_ms"], 550.25)


if __name__ == "__main__":
    unittest.main()
