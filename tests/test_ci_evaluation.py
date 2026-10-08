"""CI-P6 fixed-domain scenario replay; deterministic fixtures, no network or model."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.competitive_intelligence.evaluation import (
    FIXTURE_VERSION, load_fixtures, run_scenario, score_suite, static_pipeline,
)
from app.modules.competitive_intelligence.retention import find_unreferenced_blobs

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
FIXTURES = load_fixtures(Path(__file__).resolve().parents[1] / "evaluation" / "ci_scenarios.json")


class TestCIScenarios(unittest.TestCase):
    def test_ten_fixed_domain_scenarios(self) -> None:
        self.assertEqual(len(FIXTURES), 10)
        self.assertEqual({fixture["id"] for fixture in FIXTURES},
                         {"initial_baseline", "no_change", "feature_addition", "confirmed_removal",
                          "pricing_edit", "cosmetic_noise", "source_failure", "conflicting_numbers",
                          "out_of_scope_and_budget", "duplicate_cancel_restart"})

    def test_every_scenario_matches_annotated_truth(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(scenario=fixture["id"]):
                verdict = run_scenario(fixture, NOW)
                self.assertTrue(verdict.passed,
                                f"{fixture['id']}: outcome={verdict.outcome} kinds={verdict.candidate_kinds} "
                                f"notes={verdict.notes}")

    def test_suite_scores_without_false_alerts(self) -> None:
        scored = score_suite(FIXTURES, NOW)
        self.assertEqual(scored["failed"], [])
        self.assertEqual(scored["false_alerts"], 0)
        self.assertEqual(scored["candidate_precision_by_scenario"], 1.0)
        self.assertEqual(scored["candidate_recall"], 1.0)
        self.assertEqual(scored["fixture_version"], FIXTURE_VERSION)

    def test_static_baseline_detects_but_governs_nothing(self) -> None:
        static = static_pipeline(FIXTURES, NOW)
        self.assertEqual(static["matched_scenarios"], static["total"])
        self.assertIn("no rounds", static["note"])

    def test_mechanical_outputs_carry_no_verification_labels(self) -> None:
        from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate, RunSourceComparison
        for model in (ChangeCandidate, RunSourceComparison):
            fields = set(model.model_fields)
            self.assertTrue(fields.isdisjoint({"verification", "verified", "entailment", "truth"}))


class TestRetentionAudit(unittest.TestCase):
    def test_unreferenced_blobs_found_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp, "runs", "owner", "run", "snapshots")
            run_dir.mkdir(parents=True)
            (run_dir / "keep-captured.txt").write_text("keep", encoding="utf-8")
            (run_dir / "orphan-captured.txt").write_text("orphan", encoding="utf-8")
            brief_dir = Path(tmp, "runs", "owner", "run", "briefs")
            brief_dir.mkdir(parents=True)
            (brief_dir / "brief.md").write_text("brief", encoding="utf-8")
            referenced = {"runs/owner/run/snapshots/keep-captured.txt", "runs/owner/run/briefs/brief.md"}
            unreferenced, total = find_unreferenced_blobs(tmp, referenced)
            self.assertEqual(unreferenced, ["runs/owner/run/snapshots/orphan-captured.txt"])
            self.assertGreater(total, 0)
            # Audit never deletes.
            self.assertTrue((run_dir / "orphan-captured.txt").is_file())

    def test_artifact_roots_and_windows_names_are_safe(self) -> None:
        from app.infrastructure.artifacts.brief_blobs import BriefFileStore
        from app.infrastructure.artifacts.snapshot_blobs import SnapshotFileStore
        with tempfile.TemporaryDirectory() as tmp:
            snapshots = SnapshotFileStore(root=tmp)
            briefs = BriefFileStore(root=tmp)
            self.assertEqual(snapshots.root, briefs.root)
            uri = snapshots.put("owner/with:special?chars", "run", "snap", "normalized", "x" * 300)
            self.assertNotIn(":", uri)
            self.assertTrue((snapshots.root / uri).is_file())
            with self.assertRaises(ValueError):
                snapshots.read_span("../outside.txt", 0, 10)


if __name__ == "__main__":
    unittest.main()
