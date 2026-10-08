"""CI-P3 snapshot/normalize/diff/policy contracts; deterministic fixtures, no network or model."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from pydantic import ValidationError as SchemaError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.competitive_intelligence.snapshot_contracts import (
    ChangeCandidate, FetchOutcome, RunSourceComparison, SourceSnapshot,
)
from app.modules.competitive_intelligence.snapshot_normalize import (
    compare_snapshots, context_hash, detect_candidates, hash_text, normalize_captured, quality_gate,
    split_sections,
)
from app.modules.competitive_intelligence.snapshot_policy import (
    eligible_for_baseline, pin_baselines, promotion_plan,
)

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
PRICING_V1 = "# Pricing\n\nPro plan costs $10 per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n"
PRICING_V2 = ("# Pricing\n\nPro plan costs $12 per month, billed annually ex VAT.\n\n## Features\n\nSSO included.\n")


def outcome_fixture(status: str = "success", snapshot_id=None) -> FetchOutcome:
    return FetchOutcome(id=uuid4(), run_id=uuid4(), source_id=uuid4(), revision_id=uuid4(),
        requested_url="https://rival.invalid/pricing", final_url="https://rival.invalid/pricing", http_status=200,
        fetch_status=status, reason_codes=[], bytes_observed=512, truncated=False, snapshot_id=snapshot_id,
        attempted_at=NOW, observed_at=NOW)


def snapshot_fixture(text: str, quality: str = "eligible", reasons=None, truncated: bool = False,
                     context: str | None = None, config_version: str | None = None,
                     normalization_version: str = "ci-normalize-v1") -> tuple[SourceSnapshot, str]:
    normalized = normalize_captured(text, "pricing")
    outcome_id = uuid4()
    snapshot = SourceSnapshot(id=uuid4(), owner_id="owner", source_id=uuid4(), run_id=uuid4(),
        fetch_outcome_id=outcome_id, requested_url="https://rival.invalid/pricing",
        final_url="https://rival.invalid/pricing",
        source_context_hash=context or context_hash("https://rival.invalid/pricing",
                                                   "https://rival.invalid/pricing", "en", "US"),
        source_config_version=config_version or hash_text("config"),
        extractor_version="ci-extract-v1", normalization_version=normalization_version,
        captured_uri="runs/owner/run/snapshots/x-captured.txt", captured_hash=hash_text(text),
        normalized_uri="runs/owner/run/snapshots/x-normalized.txt", normalized_hash=hash_text(normalized),
        title="Pricing", content_type="text/plain", quality=quality, quality_reason_codes=reasons or [],
        truncated=truncated, fetched_at=NOW, observed_at=NOW)
    return snapshot, normalized


class TestSnapshotNormalization(unittest.TestCase):
    def test_whitespace_noise_collapses_but_numbers_qualifiers_survive(self) -> None:
        noisy = "# Pricing\r\n\n\nPro plan costs  $10   per month, billed annually ex VAT.  \n"
        self.assertEqual(normalize_captured(noisy, "pricing"),
                         "# Pricing\nPro plan costs  $10   per month, billed annually ex VAT.")
        self.assertIn("$10", normalize_captured(PRICING_V1, "pricing"))
        self.assertIn("ex VAT", normalize_captured(PRICING_V1, "pricing"))

    def test_unknown_normalization_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_captured("text", "pricing", normalization_version="v9")

    def test_sections_split_on_headings_with_preamble(self) -> None:
        sections = split_sections(normalize_captured("Intro line.\n\n# Pricing\n\n$10\n", "pricing"))
        self.assertEqual([section.key for section in sections], [None, "Pricing"])
        self.assertLess(sections[1].start, sections[1].end)

    def test_context_hash_stable_and_region_sensitive(self) -> None:
        left = context_hash("https://rival.invalid/pricing", "https://rival.invalid/pricing", "en", "US")
        self.assertEqual(left, context_hash("https://rival.invalid/pricing", "https://rival.invalid/pricing",
                                            "en", "US"))
        self.assertNotEqual(left, context_hash("https://rival.invalid/pricing", "https://rival.invalid/pricing",
                                               "en", "EU"))
        self.assertNotEqual(left, context_hash("https://rival.invalid/pricing", "https://mirror.invalid/pricing",
                                               "en", "US"))

    def test_quality_gate_fixtures(self) -> None:
        self.assertEqual(quality_gate("", None, None, False, "pricing"), ("ineligible", ["empty_capture"]))
        self.assertEqual(quality_gate("x" * 500, None, 200, True, "pricing"),
                         ("ineligible", ["truncated_capture"]))
        self.assertEqual(quality_gate("Please sign in to continue reading.", None, 200, False, "pricing"),
                         ("ineligible", ["challenge_page"]))
        self.assertEqual(quality_gate("Short pricing.", None, 200, False, "pricing"),
                         ("ineligible", ["below_minimum_length"]))
        self.assertEqual(quality_gate("y" * 300, None, 500, False, "pricing"),
                         ("ineligible", ["unexpected_status"]))
        self.assertEqual(quality_gate(PRICING_V1 * 4, None, 200, False, "pricing"), ("eligible", []))


class TestCompareSnapshots(unittest.TestCase):
    def test_first_eligible_capture_creates_baseline_never_a_change(self) -> None:
        current, _ = snapshot_fixture(PRICING_V1)
        comparison = compare_snapshots(current, normalize_captured(PRICING_V1, "pricing"), None, None,
                                       uuid4(), NOW)
        self.assertEqual(comparison.outcome, "baseline_created")
        self.assertIsNone(comparison.baseline_snapshot_id)

    def test_identical_hash_is_no_change(self) -> None:
        current, current_text = snapshot_fixture(PRICING_V1)
        baseline, baseline_text = snapshot_fixture(PRICING_V1)
        comparison = compare_snapshots(current, current_text, baseline, baseline_text, uuid4(), NOW)
        self.assertEqual((comparison.outcome, comparison.quality), ("no_change", "complete"))

    def test_whitespace_noise_only_is_no_change(self) -> None:
        current, current_text = snapshot_fixture("# Pricing\n\n\nPro costs $10.\n\n\n")
        baseline, baseline_text = snapshot_fixture("# Pricing\nPro costs $10.")
        self.assertEqual(current_text, baseline_text)
        comparison = compare_snapshots(current, current_text, baseline, baseline_text, uuid4(), NOW)
        self.assertEqual(comparison.outcome, "no_change")

    def test_ineligible_current_is_unavailable_never_a_removal(self) -> None:
        current, _ = snapshot_fixture("", quality="ineligible", reasons=["empty_capture"])
        baseline, baseline_text = snapshot_fixture(PRICING_V1)
        comparison = compare_snapshots(current, "", baseline, baseline_text, uuid4(), NOW)
        self.assertEqual((comparison.outcome, comparison.quality), ("unavailable", "insufficient"))

    def test_context_config_normalization_mismatch_requires_rebaseline(self) -> None:
        current, current_text = snapshot_fixture(PRICING_V2)
        baseline, baseline_text = snapshot_fixture(PRICING_V1)
        other_region = baseline.model_copy(update={"source_context_hash": context_hash(
            "https://rival.invalid/pricing", "https://rival.invalid/pricing", "en", "EU")})
        comparison = compare_snapshots(current, current_text, other_region, baseline_text, uuid4(), NOW)
        self.assertEqual(comparison.outcome, "rebaseline_required")
        other_config = baseline.model_copy(update={"source_config_version": hash_text("other")})
        comparison = compare_snapshots(current, current_text, other_config, baseline_text, uuid4(), NOW)
        self.assertEqual(comparison.outcome, "rebaseline_required")
        other_norm = baseline.model_copy(update={"normalization_version": "ci-normalize-v9"})
        comparison = compare_snapshots(current, current_text, other_norm, baseline_text, uuid4(), NOW)
        self.assertEqual((comparison.outcome, comparison.quality), ("rebaseline_required", "insufficient"))

    def test_changed_outcome_when_hashes_differ(self) -> None:
        current, current_text = snapshot_fixture(PRICING_V2)
        baseline, baseline_text = snapshot_fixture(PRICING_V1)
        comparison = compare_snapshots(current, current_text, baseline, baseline_text, uuid4(), NOW)
        self.assertEqual(comparison.outcome, "changed")


class TestDetectCandidates(unittest.TestCase):
    def test_identical_texts_yield_no_candidates(self) -> None:
        self.assertEqual(detect_candidates(PRICING_V1, PRICING_V1, uuid4(), uuid4(), uuid4(), uuid4(), NOW), [])

    def test_qualified_pricing_edit_is_one_modified_section(self) -> None:
        candidates = detect_candidates(normalize_captured(PRICING_V1, "pricing"),
                                       normalize_captured(PRICING_V2, "pricing"),
                                       uuid4(), uuid4(), uuid4(), uuid4(), NOW)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate.kind, candidate.section), ("modified", "Pricing"))
        self.assertIn("$12", candidate.after_excerpt)
        self.assertIn("ex VAT", candidate.after_excerpt)
        self.assertLessEqual(len(candidate.after_excerpt), 800)
        self.assertIsNotNone(candidate.before_start)

    def test_added_and_removed_sections(self) -> None:
        before = normalize_captured("# Pricing\n\n$10\n", "pricing")
        after = normalize_captured("# Pricing\n\n$10\n\n# Integrations\n\nSlack.\n", "pricing")
        candidates = detect_candidates(before, after, uuid4(), uuid4(), uuid4(), uuid4(), NOW)
        self.assertEqual([(c.kind, c.section) for c in candidates], [("added", "Integrations")])
        self.assertEqual(candidates[0].before_excerpt, "")
        removed = detect_candidates(after, before, uuid4(), uuid4(), uuid4(), uuid4(), NOW)
        self.assertEqual([(c.kind, c.section) for c in removed], [("removed", "Integrations")])
        self.assertEqual(removed[0].after_excerpt, "")

    def test_reordered_sections_are_not_changes_and_duplicates_dedup(self) -> None:
        before = normalize_captured("# A\n\none\n\n# B\n\ntwo\n", "pricing")
        after = normalize_captured("# B\n\ntwo\n\n# A\n\none\n", "pricing")
        self.assertEqual(detect_candidates(before, after, uuid4(), uuid4(), uuid4(), uuid4(), NOW), [])
        doubled = normalize_captured("# A\n\none\n\n# A\n\none\n", "pricing")
        single = normalize_captured("# A\n\none\n", "pricing")
        candidates = detect_candidates(single, doubled, uuid4(), uuid4(), uuid4(), uuid4(), NOW)
        self.assertLessEqual(len(candidates), 1)

    def test_candidate_contract_rejects_mixed_sides_and_unknown_fields(self) -> None:
        valid = ChangeCandidate(id=uuid4(), run_id=uuid4(), source_id=uuid4(), before_snapshot_id=uuid4(),
            after_snapshot_id=uuid4(), kind="added", before_excerpt="", after_excerpt="y",
            diff_algorithm_version="ci-section-diff-v1", diff_hash=hash_text("d"),
            detected_at=NOW).model_dump()
        with self.assertRaises(SchemaError):
            ChangeCandidate.model_validate({**valid, "before_excerpt": "oops"})
        with self.assertRaises(SchemaError):
            ChangeCandidate.model_validate({**valid, "verified": True})


class TestSnapshotPolicy(unittest.TestCase):
    def test_only_successful_eligible_captures_may_baseline(self) -> None:
        snapshot, _ = snapshot_fixture(PRICING_V1)
        outcome = outcome_fixture("success", snapshot.id)
        self.assertTrue(eligible_for_baseline(snapshot, outcome))
        for status in ("failed", "blocked", "empty"):
            self.assertFalse(eligible_for_baseline(snapshot, outcome_fixture(status, snapshot.id)))
        truncated = snapshot.model_copy(update={"truncated": True})
        self.assertFalse(eligible_for_baseline(truncated, outcome))
        challenged, _ = snapshot_fixture("x" * 300, quality="ineligible", reasons=["challenge_page"])
        self.assertFalse(eligible_for_baseline(challenged, outcome_fixture("success", challenged.id)))
        mismatched = outcome_fixture("success", uuid4())
        self.assertFalse(eligible_for_baseline(snapshot, mismatched))

    def test_promotion_plan_gates(self) -> None:
        current, current_text = snapshot_fixture(PRICING_V2)
        baseline, baseline_text = snapshot_fixture(PRICING_V1)
        changed = compare_snapshots(current, current_text, baseline, baseline_text, uuid4(), NOW)
        unavailable = compare_snapshots(*snapshot_fixture("", quality="ineligible",
                                                           reasons=["empty_capture"]),
                                        baseline, baseline_text, uuid4(), NOW)
        promoted = promotion_plan([changed, unavailable], "completed", NOW)
        self.assertEqual([c.promotion for c in promoted], ["promoted", "skipped"])
        self.assertIsNotNone(promoted[0].promoted_at)
        for terminal in ("failed", "cancelled", "interrupted"):
            halted = promotion_plan([changed], terminal, NOW)
            self.assertEqual(halted[0].promotion, "skipped")
        running = promotion_plan([changed], "running", NOW)
        self.assertEqual(running[0].promotion, "pending")

    def test_pin_baselines_never_invents_snapshots(self) -> None:
        identity = str(uuid4())
        pinned = pin_baselines(["a", "b"], {"a": identity})
        self.assertEqual(pinned, {"a": UUID(identity), "b": None})


CAPTURE_KWARGS = {"requested_url": "https://rival.invalid/pricing", "final_url": "https://rival.invalid/pricing",
                  "http_status": 200, "source_kind": "pricing", "language": "en", "region": "US",
                  "title": "Pricing", "content_type": "text/plain", "observed_at": NOW, "fetched_at": NOW}


class TestSnapshotService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = AsyncMock()
        self.source_id, self.run_id, self.revision_id = uuid4(), uuid4(), uuid4()
        from app.modules.competitive_intelligence.snapshot_service import SnapshotService
        self.service = SnapshotService(self.repository)

    def capture_kwargs(self, **overrides):
        values = {"run_id": self.run_id, "source_id": self.source_id, "revision_id": self.revision_id,
                  "owner_id": "owner", "reason_codes": [], "source_config_version": hash_text("config"),
                  **CAPTURE_KWARGS}
        values.update(overrides)
        return values

    async def test_failed_and_empty_attempts_record_outcome_without_snapshot(self) -> None:
        self.repository.record_outcome.side_effect = lambda outcome, owner: outcome
        for status, text in (("failed", None), ("blocked", "challenge"), ("empty", ""), ("success", None)):
            outcome, snapshot = await self.service.capture(
                **self.capture_kwargs(fetch_status=status, captured_text=text,
                                      http_status=None if status != "success" else 200))
            self.assertIsNone(snapshot)
            self.assertIsNone(outcome.snapshot_id)
        self.repository.capture_snapshot.assert_not_awaited()
        self.assertEqual(self.repository.record_outcome.await_count, 4)

    async def test_successful_capture_normalizes_and_gates_quality(self) -> None:
        self.repository.capture_snapshot.side_effect = lambda outcome, snapshot, *texts: snapshot
        outcome, snapshot = await self.service.capture(
            **self.capture_kwargs(fetch_status="success", captured_text=(PRICING_V1 * 4)))
        self.assertEqual(outcome.snapshot_id, snapshot.id)
        self.assertEqual(snapshot.quality, "eligible")
        self.assertEqual(snapshot.normalized_hash, hash_text(normalize_captured(PRICING_V1 * 4, "pricing")))
        self.repository.record_outcome.assert_not_awaited()

    async def test_challenge_capture_is_ineligible_but_stored(self) -> None:
        self.repository.capture_snapshot.side_effect = lambda outcome, snapshot, *texts: snapshot
        outcome, snapshot = await self.service.capture(
            **self.capture_kwargs(fetch_status="success",
                                  captured_text=("Please sign in to continue reading.\n" * 20)))
        self.assertEqual(snapshot.quality, "ineligible")
        self.assertIn("challenge_page", snapshot.quality_reason_codes)

    async def test_compare_source_creates_baseline_then_detects_change(self) -> None:
        first, first_text = snapshot_fixture(PRICING_V1 * 4)
        second, second_text = snapshot_fixture(PRICING_V2 * 4)
        self.repository.latest_baseline = AsyncMock(return_value=None)
        self.repository.get_snapshot = AsyncMock(return_value=first)
        self.repository.snapshot_text = AsyncMock(return_value=("", first_text))
        comparison, candidates = await self.service.compare_source("owner", self.run_id, self.source_id,
                                                                   first.id, NOW)
        self.assertEqual((comparison.outcome, candidates), ("baseline_created", []))
        self.repository.latest_baseline = AsyncMock(return_value=first)
        self.repository.get_snapshot = AsyncMock(return_value=second)
        self.repository.snapshot_text = AsyncMock(side_effect=[("", first_text), ("", second_text)])
        comparison, candidates = await self.service.compare_source("owner", self.run_id, self.source_id,
                                                                    second.id, NOW)
        self.assertEqual(comparison.outcome, "changed")
        self.assertEqual(len(candidates), 1)

    async def test_finalize_promotes_only_completed_runs_with_cas(self) -> None:
        current, current_text = snapshot_fixture(PRICING_V2 * 4)
        baseline, _ = snapshot_fixture(PRICING_V1 * 4)
        comparison = compare_snapshots(current, current_text, baseline, normalize_captured(PRICING_V1 * 4,
                                                                                          "pricing"),
                                       self.run_id, NOW)
        self.repository.get_snapshot = AsyncMock(return_value=current)
        self.repository.promote_baseline = AsyncMock(return_value="promoted")
        self.repository.record_comparison = AsyncMock(side_effect=lambda comparison, owner, items: comparison)
        finalized = await self.service.finalize_source("owner", comparison, [], "completed", NOW, baseline.id)
        self.assertEqual(finalized.promotion, "promoted")
        self.repository.promote_baseline.assert_awaited_once()
        halted = await self.service.finalize_source("owner", comparison, [], "failed", NOW, baseline.id)
        self.assertEqual(halted.promotion, "skipped")
        self.repository.promote_baseline.assert_awaited_once()  # No second attempt.
        self.repository.promote_baseline = AsyncMock(return_value="rejected_stale")
        stale = await self.service.finalize_source("owner", comparison, [], "completed", NOW, baseline.id)
        self.assertEqual(stale.promotion, "rejected_stale")

    async def test_prepare_pins_real_baselines_at_acceptance(self) -> None:
        from app.modules.competitive_intelligence.service import IntelligenceService
        from app.modules.runs.models import RunDocument
        from unittest.mock import MagicMock
        from app.modules.competitive_intelligence.models import (
            CreateWatchlist, Revision, Watchlist, WatchlistConfig, digest,
        )
        request = CreateWatchlist(name="W", config=WatchlistConfig(
            goal="Track pricing", dimensions=["pricing"], workflow_version_id="v",
            comparison_criteria=[{"field": "price", "objective": "Assess affordability"}],
            products=[{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/"},
                      {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                       "sources": [{"id": self.source_id, "url": "https://rival.invalid/pricing",
                                    "kind": "pricing"}]}]))
        now = NOW
        watchlist = Watchlist(id=uuid4(), owner_id="owner", name="W", description="", status="active",
            created_at=now, updated_at=now, current_revision=Revision(
                id=uuid4(), watchlist_id=uuid4(), revision_number=1, config=request.config,
                config_hash=digest(request.config), approval_status="approved", created_at=now))
        repository = AsyncMock()
        pinned_id = str(uuid4())
        repository.pin_baselines = AsyncMock(return_value={str(self.source_id): pinned_id})
        repository.workflow_id = AsyncMock(return_value="workflow")
        runs = MagicMock()
        runs.create_workflow_run = AsyncMock(return_value=RunDocument(
            run_id=str(uuid4()), flow_id="workflow", user_id="owner", workflow_version_id="v", status="queued",
            plan=[]))
        service = IntelligenceService(repository, runs)
        await service.prepare(watchlist, str(watchlist.current_revision.id), "v")
        frozen = runs.create_workflow_run.call_args.kwargs["input_data"]["competitive_intelligence"]
        self.assertEqual(frozen["baselines"], {str(self.source_id): pinned_id})
        self.assertEqual(frozen["baseline_policy"], "pinned_at_acceptance")


class TestSnapshotBlobs(unittest.TestCase):
    def test_put_read_span_and_immutability(self) -> None:
        from app.infrastructure.artifacts.snapshot_blobs import SnapshotFileStore
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotFileStore(root=tmp)
            uri = store.put("owner", "run", "snap", "normalized", "line one\nline two\n")
            self.assertTrue(uri.startswith("runs/owner/run/snapshots/"))
            self.assertEqual(store.read_span(uri, 0, 8), "line one")
            self.assertEqual(store.read_span(uri, 10**9, 10), "")
            self.assertEqual(store.read_full(uri), "line one\nline two\n")
            with self.assertRaises(ValueError):
                store.put("owner", "run", "snap", "normalized", "changed")
            with self.assertRaises(ValueError):
                store.read_span("../outside.txt", 0, 10)

    def test_migration_chain_links_0006_to_0007(self) -> None:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
        import importlib.util
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "alembic",
                                             "versions", "0007_ci_snapshots.py"))
        spec = importlib.util.spec_from_file_location("migration_0007", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "0006_ci_configuration")
        self.assertEqual(module.revision, "0007_ci_snapshots")
        self.assertTrue(callable(module.upgrade) and callable(module.downgrade))


if __name__ == "__main__":
    unittest.main()
