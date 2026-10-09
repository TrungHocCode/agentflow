"""CI-P3 snapshot use cases: capture attempts, compare against pinned baselines, promote by CAS.

No network, no model calls. Fetch bytes arrive from already-authorized collection; this
service only records, normalizes, compares and promotes them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from app.modules.competitive_intelligence.models import SourceKind
from app.modules.competitive_intelligence.ports import SnapshotRepository
from app.modules.competitive_intelligence.snapshot_contracts import (
    EXTRACTOR_VERSION, NORMALIZATION_VERSION, MAX_SPAN_CHARS, ChangeCandidate, FetchOutcome, RunSourceComparison,
    SourceSnapshot, SnapshotContent,
)
from app.modules.competitive_intelligence.snapshot_normalize import (
    compare_snapshots, context_hash, detect_candidates, hash_text, normalize_captured, quality_gate,
)
from app.modules.competitive_intelligence.snapshot_policy import eligible_for_baseline, promotion_plan
from app.shared.errors import ValidationError


class SnapshotService:
    def __init__(self, snapshots: SnapshotRepository) -> None:
        self.snapshots = snapshots

    async def capture(self, run_id: UUID, source_id: UUID, revision_id: UUID, owner_id: str,
                      requested_url: str, final_url: str | None, http_status: int | None, fetch_status: str,
                      reason_codes: list[str], captured_text: str | None, source_kind: SourceKind,
                      language: str | None, region: str | None, source_config_version: str,
                      title: str | None, content_type: str | None, observed_at: datetime,
                      fetched_at: datetime) -> tuple[FetchOutcome, SourceSnapshot | None]:
        """Record every attempt; only successful eligible captures become snapshots."""
        outcome = FetchOutcome(id=uuid4(), run_id=run_id, source_id=source_id, revision_id=revision_id,
            requested_url=requested_url, final_url=final_url, http_status=http_status,
            fetch_status=fetch_status, reason_codes=list(reason_codes),
            bytes_observed=len(captured_text.encode("utf-8")) if captured_text else 0,
            truncated=False, snapshot_id=None, attempted_at=fetched_at, observed_at=observed_at)
        if fetch_status != "success" or not captured_text:
            return await self.snapshots.record_outcome(outcome, owner_id), None
        normalized = normalize_captured(captured_text, source_kind)
        quality, quality_reasons = quality_gate(captured_text, final_url, http_status, False, source_kind)
        snapshot = SourceSnapshot(id=uuid4(), owner_id=owner_id, source_id=source_id, run_id=run_id,
            fetch_outcome_id=outcome.id, requested_url=requested_url, final_url=final_url or requested_url,
            source_context_hash=context_hash(requested_url, final_url or requested_url, language, region),
            source_config_version=source_config_version, extractor_version=EXTRACTOR_VERSION,
            normalization_version=NORMALIZATION_VERSION, captured_uri="", captured_hash=hash_text(captured_text),
            normalized_uri="", normalized_hash=hash_text(normalized), title=title, content_type=content_type,
            quality=quality, quality_reason_codes=quality_reasons, truncated=False, fetched_at=fetched_at,
            observed_at=observed_at)
        outcome = outcome.model_copy(update={"snapshot_id": snapshot.id})
        stored = await self.snapshots.capture_snapshot(outcome, snapshot, captured_text, normalized)
        return outcome, stored

    async def compare_source(self, owner_id: str, run_id: UUID, source_id: UUID, current_snapshot_id: UUID,
                             decided_at: datetime) -> tuple[RunSourceComparison, list[ChangeCandidate]]:
        """Compare the current snapshot with the pinned baseline; failures never read as removals."""
        baseline = await self.snapshots.latest_baseline(str(source_id), owner_id)
        current = await self.snapshots.get_snapshot(str(current_snapshot_id), owner_id)
        _, current_text = await self.snapshots.snapshot_text(str(current_snapshot_id), owner_id)
        baseline_text: str | None = None
        if baseline is not None:
            _, baseline_text = await self.snapshots.snapshot_text(str(baseline.id), owner_id)
        comparison = compare_snapshots(current, current_text, baseline, baseline_text, run_id, decided_at)
        candidates: list[ChangeCandidate] = []
        if comparison.outcome == "changed" and baseline is not None and baseline_text is not None:
            candidates = detect_candidates(baseline_text, current_text, run_id, source_id, baseline.id,
                                           current.id, decided_at)
        return comparison, candidates

    async def finalize_source(self, owner_id: str, comparison: RunSourceComparison,
                              candidates: list[ChangeCandidate], run_status: str, decided_at: datetime,
                              pinned_baseline_id: UUID | None) -> RunSourceComparison:
        """Apply the promotion policy, attempt the CAS advance, then record history once."""
        planned = promotion_plan([comparison], run_status, decided_at)[0]
        promotion = planned.promotion
        if promotion == "promoted" and comparison.current_snapshot_id is not None:
            current = await self.snapshots.get_snapshot(str(comparison.current_snapshot_id), owner_id)
            state = await self.snapshots.promote_baseline(current, str(comparison.run_id),
                str(pinned_baseline_id) if pinned_baseline_id else None)
            if state == "rejected_stale":
                planned = planned.model_copy(update={"promotion": "rejected_stale", "promoted_at": None,
                                                     "decided_at": decided_at})
        stored_candidates = candidates if planned.outcome == "changed" else []
        return await self.snapshots.record_comparison(planned, owner_id, stored_candidates)

    @staticmethod
    def check_eligible(snapshot: SourceSnapshot, outcome: FetchOutcome) -> bool:
        return eligible_for_baseline(snapshot, outcome)

    async def get_snapshot(self, snapshot_id: str, owner_id: str) -> SourceSnapshot:
        return await self.snapshots.get_snapshot(snapshot_id, owner_id)

    async def list_snapshots(self, source_id: str, owner_id: str, limit: int) -> list[SourceSnapshot]:
        return await self.snapshots.list_snapshots(source_id, owner_id, limit)

    async def read_content(self, snapshot_id: str, owner_id: str, representation: str, start: int,
                           limit: int) -> SnapshotContent:
        if representation not in ("captured", "normalized"):
            raise ValidationError("Snapshot representation must be captured or normalized.")
        full = await self.snapshots.snapshot_text(snapshot_id, owner_id)
        text = full[0] if representation == "captured" else full[1]
        start = max(0, start)
        bounded = text[start:start + min(max(0, limit), MAX_SPAN_CHARS)]
        snapshot = await self.snapshots.get_snapshot(snapshot_id, owner_id)
        return SnapshotContent(snapshot_id=snapshot.id, representation=representation, start_offset=start,
                               limit_chars=min(max(0, limit), MAX_SPAN_CHARS), total_chars=len(text),
                               text=bounded)

    async def get_change(self, change_id: str, owner_id: str) -> ChangeCandidate:
        return await self.snapshots.get_change(change_id, owner_id)

    async def list_comparisons(self, run_id: str, owner_id: str) -> list[RunSourceComparison]:
        return await self.snapshots.list_comparisons(run_id, owner_id)

    async def latest_baseline(self, source_id: str, owner_id: str) -> SourceSnapshot | None:
        return await self.snapshots.latest_baseline(source_id, owner_id)

    async def promote(self, snapshot: SourceSnapshot, run_id: str,
                      expected_snapshot_id: str | None) -> str:
        return await self.snapshots.promote_baseline(snapshot, run_id, expected_snapshot_id)

    async def set_promotion(self, run_id: str, source_id: str, owner_id: str, promotion: str,
                            decided_at: datetime) -> RunSourceComparison:
        return await self.snapshots.set_comparison_promotion(run_id, source_id, owner_id, promotion,
                                                             decided_at)

    async def list_changes(self, watchlist_id: str, owner_id: str, limit: int,
                           offset: int) -> tuple[list[ChangeCandidate], int]:
        return (await self.snapshots.list_changes_for_watchlist(watchlist_id, owner_id, limit, offset),
                await self.snapshots.count_changes_for_watchlist(watchlist_id, owner_id))
