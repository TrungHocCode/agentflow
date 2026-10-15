"""CI-P9 timeline assembly from durable run records; mechanical events only."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.modules.competitive_intelligence.brief_service import BriefService
from app.modules.competitive_intelligence.investigation_service import InvestigationService
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.competitive_intelligence.timeline_contracts import TimelineEvent

MAX_RUNS = 20
MAX_CANDIDATES_PER_RUN = 50
MAX_EVENTS = 200


class TimelineService:
    def __init__(self, snapshots: SnapshotService, investigations: InvestigationService,
                 briefs: BriefService, intelligence: IntelligenceService) -> None:
        self.snapshots, self.investigations = snapshots, investigations
        self.briefs, self.intelligence = briefs, intelligence

    async def competitor_timeline(self, owner_id: str, watchlist_id: UUID, source_id: UUID | None,
                                  limit: int) -> list[TimelineEvent]:
        """Merge comparisons, candidates, rounds and briefs into one time-ordered feed."""
        # Ownership is enforced by the watchlist lookup; it raises for foreign watchlists.
        await self.intelligence.get(str(watchlist_id), owner_id)
        runs = await self.intelligence.runs(str(watchlist_id), owner_id, MAX_RUNS, 0)
        events: list[TimelineEvent] = []
        for run in runs.items:
            run_id = UUID(run.run_id)
            comparisons = await self.snapshots.list_comparisons(str(run_id), owner_id)
            candidates = await self.snapshots.list_candidates(str(run_id), owner_id)
            by_source: dict[str, list] = {}
            for candidate in candidates[:MAX_CANDIDATES_PER_RUN]:
                if source_id is not None and candidate.source_id != source_id:
                    continue
                by_source.setdefault(str(candidate.source_id), []).append(candidate)
            for source_key, items in by_source.items():
                for candidate in items:
                    events.append(TimelineEvent(id=uuid4(), at=candidate.detected_at, kind="change_detected",
                        source_id=candidate.source_id,
                        summary=f"{candidate.kind} in section '{candidate.section or 'preamble'}': "
                                f"{candidate.before_excerpt} → {candidate.after_excerpt}"[:1000],
                        snapshot_ids=[candidate.before_snapshot_id, candidate.after_snapshot_id],
                        candidate_ids=[candidate.id]))
            for comparison in comparisons:
                if source_id is not None and comparison.source_id != source_id:
                    continue
                if comparison.promotion == "promoted" and comparison.current_snapshot_id is not None:
                    events.append(TimelineEvent(
                        id=uuid4(), at=comparison.promoted_at or comparison.decided_at,
                        kind="baseline_promoted", source_id=comparison.source_id,
                        summary=f"Baseline promoted after {comparison.outcome} comparison.",
                        snapshot_ids=[comparison.current_snapshot_id] if comparison.current_snapshot_id
                        else [], comparison_outcome=comparison.outcome))
            for round in await self.investigations.list_rounds(run_id, owner_id):
                if round.status in ("accepted", "completed"):
                    events.append(TimelineEvent(id=uuid4(), at=round.decided_at or round.created_at,
                        kind="round_decided", summary=f"Round {round.round_number} {round.status} "
                        f"with {len(round.tasks)} tasks.", round_id=round.id))
        briefs, _ = await self.briefs.list_briefs(str(watchlist_id), owner_id, MAX_RUNS, 0)
        for brief in briefs:
            events.append(TimelineEvent(id=uuid4(), at=brief.created_at, kind="brief_built",
                summary=f"Brief {brief.outcome}: {brief.summary}"[:1000], brief_id=brief.id,
                snapshot_ids=[entry.current_snapshot_id for entry in brief.source_coverage
                              if entry.current_snapshot_id]))
        events.sort(key=lambda event: event.at, reverse=True)
        return events[:min(max(limit, 1), MAX_EVENTS)]
