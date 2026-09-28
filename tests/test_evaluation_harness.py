import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from evaluation.record import EvaluationRunRecord, PerformanceMetrics, QualityMetrics
from scripts.evaluation_runs import (
    create_record,
    finish_record,
    import_http_log_summary,
    import_ollama_benchmark,
    import_run_metrics,
    percentile,
    render_summary,
    summarize_runs,
    write_record,
)


class TestEvaluationRecord(unittest.TestCase):
    def make_record(self, **overrides):
        now = datetime.now(timezone.utc)
        values = {
            "evaluation_id": "test-evaluation-1",
            "case_id": "llm-benchmark-comparison",
            "started_at": now,
            "finished_at": now + timedelta(seconds=2),
            "configuration": {"model": "qwen3:8b", "mode": "live"},
            "execution_status": "partial",
            "evaluation_verdict": "needs_review",
            "performance": {"end_to_end_ms": 2000},
            "quality": {
                "requested_dimensions": 5,
                "covered_dimensions": 3,
                "sources_found": 2,
                "sources_validated": 1,
                "factual_claims": 10,
                "claims_with_citations": 8,
                "unsupported_claims": 1,
            },
        }
        values.update(overrides)
        return EvaluationRunRecord(**values)

    def test_record_rejects_naive_timestamps_and_impossible_lifecycle(self):
        with self.assertRaises(ValidationError):
            self.make_record(started_at=datetime.now())
        with self.assertRaises(ValidationError):
            self.make_record(finished_at=None, execution_status="completed")

    def test_quality_counts_cannot_exceed_totals(self):
        with self.assertRaises(ValidationError):
            self.make_record(quality={"requested_dimensions": 2, "covered_dimensions": 3})
        with self.assertRaises(ValidationError):
            self.make_record(quality={"covered_dimensions": 1})

    def test_previous_record_schema_version_remains_readable(self):
        legacy = self.make_record().model_dump(mode="json")
        legacy["schema_version"] = "1.0"
        restored = EvaluationRunRecord.model_validate(legacy)
        self.assertEqual(restored.schema_version, "1.0")
        self.assertEqual(restored.performance.llm_calls, [])

    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([100, 300, 200, 400], 0.5), 200.0)
        self.assertEqual(percentile([100, 300, 200, 400], 0.95), 400.0)
        self.assertIsNone(percentile([], 0.95))

    def test_summary_includes_latency_coverage_and_verdict(self):
        report = render_summary([self.make_record()])
        self.assertIn("2000 ms", report)
        self.assertIn("60%", report)
        self.assertIn("50%", report)
        self.assertIn("80%", report)
        self.assertIn("1 |", report)
        self.assertIn("0/1/0", report)

    def test_import_run_metrics_maps_private_runtime_timings(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            record_path = Path(temporary_dir) / "evaluation.json"
            write_record(record_path, self.make_record())
            imported = import_run_metrics(
                record_path,
                {
                    "run_id": "agentflow-run-5",
                    "execution_time_ms": 1400,
                    "metadata": {
                        "run_timing_metrics": {
                            "queue_wait_ms": 120,
                            "workflow_execution_ms": 1400,
                            "end_to_end_ms": 1900,
                        },
                        "execution_timings": [
                            {
                                "operation": "llm",
                                "phase": "plan",
                                "duration_ms": 600,
                                "status": "success",
                            },
                            {
                                "operation": "tool",
                                "name": "web_search",
                                "duration_ms": 200,
                                "status": "success",
                            },
                            {
                                "operation": "tool",
                                "name": "web_search",
                                "duration_ms": 300,
                                "status": "failed",
                                "error_type": "ReadTimeout",
                            },
                        ],
                        "task_execution_metrics": [
                            {
                                "node": "researcher",
                                "duration_ms": 900,
                                "status": "done",
                            }
                        ],
                        "task_metrics": {"critical_path_ms": 900},
                        "llm_call_metrics": [
                            {
                                "component": "Supervisor",
                                "purpose": "planner",
                                "status": "success",
                                "request_latency_ms": 650,
                                "raw_ttft_ms": 100,
                                "itl_p50_ms": 25,
                                "itl_p95_ms": 40,
                                "output_tokens": 30,
                                "output_tokens_per_second": 50,
                            }
                        ],
                    },
                },
                {
                    "last_turn": {
                        "turn_id": "turn-last",
                        "planning_duration_ms": 2500,
                    },
                    "chat_ttft_samples": [
                        {"turn_id": "turn-old", "ttft_ms": 999},
                        {"turn_id": "turn-last", "ttft_ms": 180},
                    ],
                    "llm_call_metrics": [
                        {
                            "call_id": "chat-plan-call",
                            "component": "Supervisor",
                            "purpose": "planner",
                            "status": "success",
                            "request_latency_ms": 700,
                            "raw_ttft_ms": 120,
                        }
                    ],
                },
            )

            self.assertEqual(imported.agentflow_run_id, "agentflow-run-5")
            self.assertEqual(imported.performance.queue_wait_ms, 120)
            self.assertEqual(imported.performance.end_to_end_ms, 1900)
            self.assertEqual(imported.performance.plan_generation_ms, 2500)
            self.assertEqual(imported.performance.supervisor_ttft_ms, 180)
            self.assertEqual(imported.performance.tool_timings[0].calls, 2)
            self.assertEqual(imported.performance.tool_timings[0].failures, 1)
            self.assertEqual(imported.performance.tool_timings[0].timeouts, 1)
            self.assertEqual(imported.performance.node_timings[0].name, "researcher")
            self.assertEqual(imported.performance.critical_path_ms, 900)
            imported_raw_ttfts = {call.raw_ttft_ms for call in imported.performance.llm_calls}
            self.assertEqual(imported_raw_ttfts, {100, 120})
            self.assertEqual(imported.execution_status, "partial")
            self.assertEqual(
                EvaluationRunRecord.model_validate_json(record_path.read_text()).agentflow_run_id,
                "agentflow-run-5",
            )

    def test_import_ollama_benchmark_attaches_rps_and_inference_metrics(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            record_path = Path(temporary_dir) / "evaluation.json"
            benchmark_path = Path(temporary_dir) / "benchmark.json"
            write_record(record_path, self.make_record())
            benchmark_path.write_text(
                json.dumps(
                    {
                        "model": "qwen3:8b",
                        "concurrency": 2,
                        "offered_requests_per_second": 1.5,
                        "observed_requests_per_second": 1.2,
                        "test_duration_seconds": 5,
                        "warmup_requests": 1,
                        "warmup_successful_requests": 1,
                        "warmup_duration_seconds": 2.4,
                        "request_count": 6,
                        "successful_requests": 5,
                        "failed_requests": 1,
                        "timed_out_requests": 1,
                        "aggregate_output_tokens_per_second": 22,
                        "resources": {
                            "cpu_utilization_percent": 55,
                            "peak_ram_mb": 9000,
                            "gpu_name": "NVIDIA Test GPU",
                            "gpu_utilization_percent": 80,
                            "peak_vram_mb": 7600,
                            "sample_count": 3,
                        },
                        "samples": [
                            {
                                "status": "success",
                                "request_latency_ms": 1100,
                                "raw_ttft_ms": 300,
                                "generation_ms": 700,
                                "itl_p50_ms": 40,
                                "itl_p95_ms": 90,
                                "output_tokens": 15,
                                "output_tokens_per_second": 21.4,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            imported = import_ollama_benchmark(record_path, benchmark_path)

            self.assertEqual(imported.performance.load_test.target, "llm")
            self.assertEqual(imported.performance.load_test.observed_requests_per_second, 1.2)
            self.assertEqual(imported.performance.load_test.successful_requests, 5)
            self.assertEqual(imported.performance.load_test.warmup_requests, 1)
            self.assertEqual(imported.performance.llm_calls[0].raw_ttft_ms, 300)
            self.assertEqual(imported.performance.llm_calls[0].itl_p95_ms, 90)
            self.assertEqual(imported.performance.llm_calls[0].output_tokens, 15)
            self.assertEqual(imported.resources.peak_vram_mb, 7600)
            self.assertEqual(imported.resources.gpu_name, "NVIDIA Test GPU")

    def test_benchmark_import_rejects_model_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            record_path = Path(temporary_dir) / "evaluation.json"
            benchmark_path = Path(temporary_dir) / "benchmark.json"
            write_record(record_path, self.make_record())
            benchmark_path.write_text(json.dumps({"model": "gemma3:8b"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Model mismatch"):
                import_ollama_benchmark(record_path, benchmark_path)

    def test_http_import_attaches_route_latency_and_api_rps(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            record_path = Path(temporary_dir) / "evaluation.json"
            summary_path = Path(temporary_dir) / "http-summary.json"
            write_record(record_path, self.make_record())
            summary_path.write_text(
                json.dumps(
                    {
                        "measurement_window_seconds": 10,
                        "routes": [
                            {
                                "method": "GET",
                                "route": "/api/v1/runs/{id}",
                                "request_count": 20,
                                "successful_request_count": 18,
                                "client_error_count": 1,
                                "server_error_count": 1,
                                "observed_requests_per_second": 2,
                                "latency_ms": {"p50": 80, "p95": 240},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            imported = import_http_log_summary(
                record_path,
                summary_path,
                route="/api/v1/runs/{id}",
                method="GET",
            )

            self.assertEqual(imported.performance.api_response_ms, 80)
            self.assertEqual(imported.performance.api_response_p95_ms, 240)
            self.assertEqual(imported.performance.load_test.target, "api")
            self.assertEqual(imported.performance.load_test.observed_requests_per_second, 2)
            self.assertEqual(imported.performance.load_test.failed_requests, 2)

    def test_create_finish_validate_record_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            runs_dir = Path(temporary_dir) / "runs"
            path = create_record(
                case_id="llm-benchmark-comparison",
                model="qwen3:8b",
                mode="replay",
                runs_dir=runs_dir,
                evaluation_id="stable-test-id",
            )
            draft = EvaluationRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            self.assertEqual(draft.execution_status, "in_progress")

            finished = finish_record(path, "completed", "pass", agentflow_run_id="app-run-1")
            self.assertEqual(finished.execution_status, "completed")
            self.assertEqual(finished.evaluation_verdict, "pass")
            self.assertEqual(finished.agentflow_run_id, "app-run-1")
            self.assertEqual(
                EvaluationRunRecord.model_validate_json(path.read_text()).evaluation_id,
                "stable-test-id",
            )

    def test_record_defaults_to_mode_declared_by_case(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = create_record(
                case_id="report-and-chart-from-data",
                model="qwen3:8b",
                runs_dir=Path(temporary_dir),
            )
            record = EvaluationRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            self.assertEqual(record.configuration.mode, "replay")

    def test_summary_command_writes_aggregate_report(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            runs_dir = Path(temporary_dir) / "runs"
            path = create_record(
                case_id="llm-benchmark-comparison",
                model="qwen3:8b",
                runs_dir=runs_dir,
                evaluation_id="summary-test-id",
            )
            record = finish_record(path, "partial", "needs_review")
            record = record.model_copy(
                update={
                    "performance": PerformanceMetrics(
                        end_to_end_ms=45000,
                        load_test={
                            "observed_requests_per_second": 1.5,
                            "completed_runs": 4,
                            "test_duration_seconds": 120,
                        },
                    ),
                    "quality": QualityMetrics(requested_dimensions=4, covered_dimensions=3),
                }
            )
            write_record(path, EvaluationRunRecord.model_validate(record.model_dump()))

            report_path, valid_count, invalid_count = summarize_runs(
                runs_dir,
                Path(temporary_dir) / "reports" / "summary.md",
            )

            report = report_path.read_text(encoding="utf-8")
            self.assertEqual((valid_count, invalid_count), (1, 0))
            self.assertIn("45000 ms", report)
            self.assertIn("75%", report)
            self.assertIn("2 |", report)
        self.assertIn("1.5 |", report)
        self.assertIn("LLM inference and throughput", report)

    def test_all_case_fixtures_are_valid_json_and_have_acceptance_criteria(self):
        root = Path(__file__).resolve().parents[1]
        case_paths = sorted((root / "evaluation" / "cases").glob("*.json"))
        self.assertEqual(len(case_paths), 5)
        for case_path in case_paths:
            case = json.loads(case_path.read_text(encoding="utf-8"))
            self.assertEqual(case["case_id"], case_path.stem)
            self.assertIn(case["mode"], {"live", "replay"})
            self.assertTrue(case["requested_dimensions"])
            self.assertTrue(case["acceptance_criteria"])

        schema_path = root / "evaluation" / "run-record.schema.json"
        self.assertEqual(
            json.loads(schema_path.read_text(encoding="utf-8")),
            EvaluationRunRecord.model_json_schema(),
        )


if __name__ == "__main__":
    unittest.main()
