"""CI-P5 brief use cases: assemble typed briefs, render deterministically, serve downloads.

Builds run only for completed runs owned by the caller; the brief never labels a run
fully verified — findings carry quotation-level labels only.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.modules.competitive_intelligence.brief_contracts import (
    BRIEF_SCHEMA_VERSION, BriefFinding, BriefSourceCoverage, IntelligenceBrief,
)
from app.modules.competitive_intelligence.brief_render import advisory_actions, render_markdown
from app.modules.competitive_intelligence.ports import BriefRepository, SnapshotRepository
from app.modules.runs.service import RunService
from app.shared.errors import ConflictError, ResourceNotFoundError


class BriefService:
    def __init__(self, briefs: BriefRepository, snapshots: SnapshotRepository, blobs,
                 runs: RunService) -> None:
        self.briefs, self.snapshots, self.blobs, self.runs = briefs, snapshots, blobs, runs

    @staticmethod
    def derive_outcome(outcomes: list[str]) -> tuple[str, str]:
        """Priority: changes_detected > partial > rebaseline_required > baseline_created > no_change."""
        if not outcomes:
            return "unavailable", "insufficient"
        if "changed" in outcomes:
            code = "changes_detected"
        elif "unavailable" in outcomes:
            code = "partial"
        elif "rebaseline_required" in outcomes:
            code = "rebaseline_required"
        elif "baseline_created" in outcomes:
            code = "baseline_created"
        else:
            code = "no_change"
        quality = ("complete" if code in ("changes_detected", "no_change", "baseline_created")
                   else "partial" if code in ("partial", "rebaseline_required") else "insufficient")
        return code, quality

    async def build_brief(self, watchlist_id: UUID, revision_id: UUID, run_id: UUID, owner_id: str,
                          created_at: datetime) -> IntelligenceBrief:
        """Assemble, render, store and record the brief for one completed owned run."""
        run = await self.runs.get_run(str(run_id), owner_id)
        if run is None:
            raise ResourceNotFoundError("Run was not found.", entity="run")
        if run.status != "completed":
            raise ConflictError("Briefs require a completed run.", code="brief_run_not_completed")
        if run.watchlist_id != str(watchlist_id) or run.watchlist_revision_id != str(revision_id):
            raise ConflictError("Run does not belong to this watchlist revision.", code="brief_scope_mismatch")
        comparisons = await self.snapshots.list_comparisons(str(run_id), owner_id)
        candidates = await self.snapshots.list_candidates(str(run_id), owner_id)
        outcome, quality = self.derive_outcome([comparison.outcome for comparison in comparisons])
        coverage = [BriefSourceCoverage(source_id=comparison.source_id,
            baseline_snapshot_id=comparison.baseline_snapshot_id,
            current_snapshot_id=comparison.current_snapshot_id, outcome=comparison.outcome,
            quality="complete" if comparison.quality == "complete" else "insufficient",
            reason_codes=list(comparison.reason_codes), observed_at=comparison.decided_at)
            for comparison in comparisons]
        findings = [BriefFinding(id=uuid4(),
            text=f"{candidate.kind.title()} in section '{candidate.section or 'preamble'}': "
                 f"{candidate.before_excerpt} → {candidate.after_excerpt}".strip()[:1000],
            verification="observed",
            rationale=f"Candidate {candidate.id} from deterministic section diff "
                      f"{candidate.diff_algorithm_version}.".strip()[:2000],
            candidate_id=candidate.id, before_snapshot_id=candidate.before_snapshot_id,
            after_snapshot_id=candidate.after_snapshot_id,
            evidence_offsets=[offset for offset in (candidate.before_start, candidate.after_start)
                              if offset is not None])
            for candidate in candidates
            if next((c.outcome == "changed" for c in comparisons if c.source_id == candidate.source_id), False)]
        limitations = [f"Source {comparison.source_id} {comparison.outcome}: "
                       f"{', '.join(comparison.reason_codes) or 'no detail'}."
                       for comparison in comparisons if comparison.outcome in ("unavailable", "rebaseline_required")]
        changed_sources = sorted({str(c.source_id) for c in comparisons if c.outcome == "changed"})
        summary = (f"Run compared {len(comparisons)} source(s): "
                   f"{len(changed_sources)} changed, {len(findings)} finding(s). "
                   f"Changed sources: {', '.join(changed_sources) or 'none'}.")[:8000]
        decided = [comparison.decided_at for comparison in comparisons] or [created_at]
        brief_id = uuid4()
        brief = IntelligenceBrief(id=brief_id, watchlist_id=watchlist_id, revision_id=revision_id,
            run_id=run_id, schema_version=BRIEF_SCHEMA_VERSION, outcome=outcome, quality=quality,
            summary=summary, findings=findings, source_coverage=coverage, conflicts=[],
            limitations=limitations, advisory_actions=advisory_actions(outcome),
            artifact_id=str(brief_id), artifact_uri="", artifact_hash="0" * 64,
            observed_from=min(decided), observed_to=max(decided), created_at=created_at)
        text = render_markdown(brief)
        uri = self.blobs.put(owner_id, str(run_id), str(brief_id), text)
        stored = brief.model_copy(update={"artifact_uri": uri,
                                          "artifact_hash": hashlib.sha256(text.encode()).hexdigest()})
        return await self.briefs.save_brief(stored, owner_id)

    async def get_brief(self, brief_id: str, owner_id: str) -> IntelligenceBrief:
        return await self.briefs.get_brief(brief_id, owner_id)

    async def list_briefs(self, watchlist_id: str, owner_id: str, limit: int,
                          offset: int) -> tuple[list[IntelligenceBrief], int]:
        return (await self.briefs.list_briefs(watchlist_id, owner_id, limit, offset),
                await self.briefs.count_briefs(watchlist_id, owner_id))

    async def download_brief(self, brief_id: str, owner_id: str) -> tuple[Path, str]:
        brief = await self.briefs.get_brief(brief_id, owner_id)
        return self.blobs.resolve(brief.artifact_uri), f"brief-{brief.id}.md"
