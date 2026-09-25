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
