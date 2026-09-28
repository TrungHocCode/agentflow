import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.runs.models import RunDocument, RunResponse
from app.modules.conversations.models import ConversationResponse
from app.modules.conversations.models import ConversationRecord
from app.modules.conversations.service import ConversationService
from app.modules.runs.service import RunService
from app.core.config import settings
from app.shared.execution_metrics import (
    ExecutionTiming,
    merge_execution_timings,
    summarize_execution_timings,
)
from app.shared.task_metrics import (
    TaskExecutionMetric,
    summarize_task_execution_metrics,
)
from app.shared.llm_call_metrics import LLMCallMetric


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
        error_type: str | None = None,
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
            error_type=error_type,
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
            self._timing(
                operation="tool",
                name="http_request",
                duration_ms=800,
                status="failed",
                error_type="ReadTimeout",
            ),
        ]

        summary = summarize_execution_timings(spans)

        self.assertEqual(summary["llm"]["call_count"], 2)
        self.assertEqual(summary["llm"]["total_ms"], 1900)
        self.assertEqual(summary["tools"]["call_count"], 3)
        self.assertEqual(summary["tools"]["total_ms"], 1600)
        self.assertEqual(summary["tools"]["timeout_count"], 1)
        self.assertEqual(summary["tools"]["partial_count"], 1)
        self.assertEqual(summary["tools"]["by_tool"]["news_crawler"]["total_ms"], 500)
        self.assertEqual(summary["phase_totals_ms"]["plan"]["llm_ms"], 700)
        self.assertEqual(summary["phase_totals_ms"]["execute"]["total_ms"], 2800)

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

    def test_run_service_accumulates_llm_and_task_metrics_in_private_metadata(self):
        now = datetime.now(timezone.utc)
        call = LLMCallMetric(
            call_id="llm-1",
            component="researcher",
            purpose="worker",
            model="qwen3:8b",
            task_id=1,
            status="success",
            started_at=now,
            completed_at=now,
            request_latency_ms=900,
            raw_ttft_ms=150,
            generation_ms=500,
            itl_p50_ms=30,
            input_tokens=100,
            output_tokens=20,
            output_tokens_per_second=40,
        ).model_dump(mode="json")
        task = TaskExecutionMetric(
            task_id=1,
            node="researcher",
            status="done",
            duration_ms=1200,
            started_at=now,
            completed_at=now,
        ).model_dump(mode="json")
        run = RunDocument(run_id="run-1", flow_id="flow-1", status="running")
        service = object.__new__(RunService)

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            events = service._apply_execution_output(
                run,
                "researcher_node",
                {"llm_call_metrics": [call], "task_execution_metrics": [task]},
            )

        self.assertEqual(run.metadata["llm_inference_metrics"]["raw_ttft_p50_ms"], 150)
        self.assertEqual(run.metadata["task_metrics"]["critical_path_ms"], 1200)
        self.assertEqual(events, [])

    def test_conversation_accumulates_llm_metrics_only_for_internal_storage(self):
        now = datetime.now(timezone.utc)
        conversation = ConversationRecord(
            id="conversation-1",
            created_at=now,
            updated_at=now,
        )
        call = LLMCallMetric(
            call_id="chat-call-1",
            component="Supervisor",
            purpose="planner",
            model="qwen3:8b",
            status="success",
            started_at=now,
            completed_at=now,
            request_latency_ms=600,
            raw_ttft_ms=80,
        ).model_dump(mode="json")

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            ConversationService._accumulate_llm_call_metrics(
                conversation,
                {"llm_call_metrics": [call]},
            )

        self.assertEqual(conversation.metadata["llm_call_metrics"][0]["call_id"], "chat-call-1")
        self.assertEqual(conversation.metadata["llm_inference_metrics"]["raw_ttft_p50_ms"], 80)

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
                "llm_call_metrics": [{"call_id": "call-1"}],
                "llm_inference_metrics": {"call_count": 1},
                "last_turn": {
                    "turn_id": "turn-1",
                    "planning_duration_ms": 900,
                },
                "supervisor_decision": "answer",
            },
        )

        self.assertEqual(
            response.metadata,
            {
                "last_turn": {"turn_id": "turn-1"},
                "supervisor_decision": "answer",
            },
        )

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
                "llm_call_metrics": [{"call_id": "call-1"}],
                "llm_inference_metrics": {"call_count": 1},
                "task_execution_metrics": [{"task_id": 1}],
                "task_metrics": {"task_count": 1},
                "run_timing_metrics": {"queue_wait_ms": 10},
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

    def test_task_summary_calculates_dag_critical_path_not_total_parallel_work(self):
        now = datetime.now(timezone.utc)
        timings = [
            TaskExecutionMetric(
                task_id=1,
                node="source_researcher",
                status="done",
                duration_ms=1000,
                started_at=now,
                completed_at=now,
            ),
            TaskExecutionMetric(
                task_id=2,
                node="source_researcher",
                status="done",
                duration_ms=2000,
                started_at=now,
                completed_at=now,
            ),
            TaskExecutionMetric(
                task_id=3,
                node="report_agent",
                status="done",
                duration_ms=500,
                started_at=now,
                completed_at=now,
            ),
        ]
        summary = summarize_task_execution_metrics(
            timings,
            {1: [], 2: [], 3: [1, 2]},
        )
        self.assertEqual(summary["total_task_ms"], 3500)
        self.assertEqual(summary["critical_path_ms"], 2500)
        self.assertEqual(summary["task_count"], 3)

    def test_finalize_records_queue_workflow_and_end_to_end_boundaries(self):
        run = RunDocument(
            run_id="run-timing",
            flow_id="flow-1",
            execution_time_ms=1200,
            metadata={
                "request_started_at": "2026-01-01T00:00:00+00:00",
                "queued_at": "2026-01-01T00:00:00.100+00:00",
                "worker_started_at": "2026-01-01T00:00:00.300+00:00",
            },
        )
        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            RunService._finalize_run_timing_metrics(run)

        self.assertAlmostEqual(run.metadata["queue_wait_ms"], 200)
        self.assertEqual(run.metadata["workflow_execution_ms"], 1200)
        self.assertGreater(run.metadata["end_to_end_ms"], 0)


class TestTerminalRunMetrics(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_run_finalizes_wall_clock_metrics(self):
        now = datetime.now(timezone.utc)
        run = RunDocument(
            run_id="run-cancelled",
            flow_id="flow-1",
            status="running",
            metadata={
                "request_started_at": (now - timedelta(seconds=3)).isoformat(),
                "queued_at": (now - timedelta(seconds=2)).isoformat(),
                "worker_started_at": (now - timedelta(seconds=1)).isoformat(),
            },
        )
        service = object.__new__(RunService)
        service.run_repository = SimpleNamespace(save=AsyncMock())
        service.get_run = AsyncMock(return_value=run)
        service._record_event = AsyncMock()

        with patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            cancelled = await service.cancel_run(run.run_id)

        self.assertEqual(cancelled.status, "cancelled")
        self.assertGreater(cancelled.metadata["workflow_execution_ms"], 0)
        self.assertGreater(cancelled.metadata["end_to_end_ms"], 0)
        self.assertIn("finished_at", cancelled.metadata["run_timing_metrics"])


if __name__ == "__main__":
    unittest.main()
