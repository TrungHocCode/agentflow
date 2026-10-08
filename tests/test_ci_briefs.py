"""CI-P5 typed brief assembly, deterministic rendering and read APIs; no network or model."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.api.dependencies import (
    get_brief_service, get_current_user_id, get_investigation_service, get_snapshot_service,
)
from app.infrastructure.artifacts.brief_blobs import BriefFileStore
from app.main import app
from app.modules.competitive_intelligence.brief_contracts import IntelligenceBrief
from app.modules.competitive_intelligence.brief_render import advisory_actions, render_markdown
from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate, RunSourceComparison
from app.shared.errors import ResourceNotFoundError

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def comparison_fixture(outcome: str, source_id=None) -> RunSourceComparison:
    return RunSourceComparison(run_id=uuid4(), source_id=source_id or uuid4(), baseline_snapshot_id=uuid4(),
        current_snapshot_id=uuid4(), outcome=outcome, quality="complete" if outcome != "unavailable"
        else "insufficient", reason_codes=[], decided_at=NOW)


def candidate_fixture(comparison: RunSourceComparison) -> ChangeCandidate:
    return ChangeCandidate(id=uuid4(), run_id=comparison.run_id, source_id=comparison.source_id,
        before_snapshot_id=comparison.baseline_snapshot_id, after_snapshot_id=comparison.current_snapshot_id,
        kind="modified", section="Pricing", before_start=0, before_end=10, after_start=0, after_end=12,
        before_excerpt="Pro costs $10.", after_excerpt="Pro costs $12.",
        diff_algorithm_version="ci-section-diff-v1", diff_hash="d" * 64, detected_at=NOW)


def brief_fixture(**overrides) -> IntelligenceBrief:
    values = {"id": uuid4(), "watchlist_id": uuid4(), "revision_id": uuid4(), "run_id": uuid4(),
              "outcome": "changes_detected", "quality": "complete", "summary": "Run compared 1 source(s).",
              "artifact_id": "brief-x", "artifact_uri": "runs/o/r/briefs/b.md", "artifact_hash": "h" * 64,
              "observed_from": NOW, "observed_to": NOW, "created_at": NOW}
    values.update(overrides)
    return IntelligenceBrief(**values)


class TestBriefOutcome(unittest.TestCase):
    def test_derive_outcome_priority(self) -> None:
        derive = BriefService.derive_outcome
        self.assertEqual(derive(["no_change"]), ("no_change", "complete"))
        self.assertEqual(derive(["no_change", "baseline_created"]), ("baseline_created", "complete"))
        self.assertEqual(derive(["no_change", "rebaseline_required"]), ("rebaseline_required", "partial"))
        self.assertEqual(derive(["no_change", "unavailable"]), ("partial", "partial"))
        self.assertEqual(derive(["changed", "unavailable"]), ("changes_detected", "complete"))
        self.assertEqual(derive([]), ("unavailable", "insufficient"))

    def test_render_is_deterministic_and_never_claims_full_verification(self) -> None:
        brief = brief_fixture()
        self.assertEqual(render_markdown(brief), render_markdown(brief))
        text = render_markdown(brief)
        self.assertIn("Outcome: changes_detected", text)
        self.assertIn("## Per-source coverage", text)
        self.assertIn("## Advisory actions", text)
        self.assertNotIn("all findings verified", text.lower())
        self.assertTrue(advisory_actions("changes_detected"))


class TestBriefService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.briefs, self.snapshots = AsyncMock(), AsyncMock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = MagicMock()
        self.service = BriefService(self.briefs, self.snapshots, BriefFileStore(root=self.tmp.name), self.runs)

    def run_doc(self, status="completed", watchlist_id=None, revision_id=None):
        document = MagicMock()
        document.status = status
        document.watchlist_id = str(watchlist_id or uuid4())
        document.watchlist_revision_id = str(revision_id or uuid4())
        return document

    async def test_build_assembles_findings_limitations_and_artifact(self) -> None:
        changed = comparison_fixture("changed")
        quiet = comparison_fixture("no_change")
        missing = comparison_fixture("unavailable")
        watchlist_id, revision_id = uuid4(), uuid4()
        self.runs.get_run = AsyncMock(return_value=self.run_doc("completed", watchlist_id, revision_id))
        self.snapshots.list_comparisons = AsyncMock(return_value=[changed, quiet, missing])
        self.snapshots.list_candidates = AsyncMock(return_value=[candidate_fixture(changed)])
        self.briefs.save_brief = AsyncMock(side_effect=lambda brief, owner: brief)
        brief = await self.service.build_brief(watchlist_id, revision_id, uuid4(), "owner", NOW)
        self.assertEqual((brief.outcome, brief.quality), ("changes_detected", "complete"))
        self.assertEqual(len(brief.findings), 1)
        self.assertEqual(brief.findings[0].verification, "observed")
        self.assertTrue(brief.limitations)
        self.assertTrue(brief.advisory_actions)
        self.assertTrue(Path(self.tmp.name, brief.artifact_uri).is_file())
        self.assertEqual(len(brief.artifact_hash), 64)

    async def test_build_rejects_unowned_incomplete_or_mismatched_runs(self) -> None:
        from app.shared.errors import ConflictError
        self.runs.get_run = AsyncMock(return_value=None)
        with self.assertRaises(ResourceNotFoundError):
            await self.service.build_brief(uuid4(), uuid4(), uuid4(), "owner", NOW)
        self.runs.get_run = AsyncMock(return_value=self.run_doc("failed", uuid4(), uuid4()))
        with self.assertRaises(ConflictError):
            await self.service.build_brief(uuid4(), uuid4(), uuid4(), "owner", NOW)
        self.runs.get_run = AsyncMock(return_value=self.run_doc())
        with self.assertRaises(ConflictError):
            await self.service.build_brief(uuid4(), uuid4(), uuid4(), "owner", NOW)

    async def test_download_resolves_owner_checked_file(self) -> None:
        brief = brief_fixture()
        target = Path(self.tmp.name, brief.artifact_uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# brief\n", encoding="utf-8")
        self.briefs.get_brief = AsyncMock(return_value=brief)
        path, filename = await self.service.download_brief(str(brief.id), "owner")
        self.assertEqual((path, filename), (target.resolve(), f"brief-{brief.id}.md"))


class TestBriefAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.briefs, self.snapshots, self.rounds = AsyncMock(), AsyncMock(), AsyncMock()
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.dependency_overrides[get_brief_service] = lambda: self.briefs
        app.dependency_overrides[get_snapshot_service] = lambda: self.snapshots
        app.dependency_overrides[get_investigation_service] = lambda: self.rounds
        self.addCleanup(app.dependency_overrides.clear)

    async def test_brief_and_snapshot_reads(self) -> None:
        brief = brief_fixture()
        self.briefs.get_brief = AsyncMock(return_value=brief)
        self.briefs.list_briefs = AsyncMock(return_value=([brief], 1))
        self.snapshots.list_changes = AsyncMock(return_value=([], 0))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/briefs/{brief.id}")
            self.assertEqual(response.status_code, 200, response.text)
            response = await client.get(f"/api/v1/watchlists/{brief.watchlist_id}/briefs")
            self.assertEqual(response.json()["pagination"]["total"], 1)
            response = await client.get(f"/api/v1/watchlists/{brief.watchlist_id}/changes")
            self.assertEqual(response.status_code, 200, response.text)
            self.snapshots.get_snapshot = AsyncMock(side_effect=ResourceNotFoundError("gone"))
            self.snapshots.read_content = AsyncMock(side_effect=ResourceNotFoundError("gone"))
            hidden = await client.get(f"/api/v1/snapshots/{uuid4()}")
            self.assertEqual(hidden.status_code, 404)
            denied = await client.get(f"/api/v1/snapshots/{uuid4()}/content")
            self.assertEqual(denied.status_code, 404)

    async def test_build_and_download_endpoints(self) -> None:
        from pathlib import Path as FsPath
        brief = brief_fixture()
        self.briefs.build_brief = AsyncMock(return_value=brief)
        target = FsPath(tempfile.mkdtemp()) / "brief.md"
        target.write_bytes(b"# brief\n")
        self.briefs.download_brief = AsyncMock(return_value=(target, "brief.md"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/watchlists/{brief.watchlist_id}/briefs",
                json={"run_id": str(brief.run_id), "revision_id": str(brief.revision_id)})
            self.assertEqual(response.status_code, 201, response.text)
            response = await client.get(f"/api/v1/briefs/{brief.id}/download")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, b"# brief\n")
            self.rounds.list_rounds = AsyncMock(return_value=[])
            response = await client.get(f"/api/v1/runs/{brief.run_id}/investigation-rounds")
            self.assertEqual(response.json(), [])

    def test_migration_chain_links_0008_to_0009(self) -> None:
        import importlib.util
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "alembic",
                                             "versions", "0009_ci_briefs.py"))
        spec = importlib.util.spec_from_file_location("migration_0009", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "0008_ci_investigation")
        self.assertEqual(module.revision, "0009_ci_briefs")


if __name__ == "__main__":
    unittest.main()
