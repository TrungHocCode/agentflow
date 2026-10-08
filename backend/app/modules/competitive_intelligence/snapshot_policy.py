"""Baseline eligibility and promotion policy; failed/cancelled runs never advance pointers."""

from datetime import datetime
from uuid import UUID

from app.modules.competitive_intelligence.snapshot_contracts import FetchOutcome, RunSourceComparison, SourceSnapshot

TERMINAL_FAILURE = ("failed", "cancelled", "interrupted", "abandoned")
PROMOTABLE_OUTCOMES = ("baseline_created", "no_change", "changed")


def eligible_for_baseline(snapshot: SourceSnapshot, outcome: FetchOutcome) -> bool:
    """Only a successful, eligible, non-truncated capture may become a comparison baseline."""
    return (outcome.fetch_status == "success" and outcome.snapshot_id == snapshot.id
            and snapshot.quality == "eligible" and not snapshot.truncated)


def promotion_plan(comparisons: list[RunSourceComparison], run_status: str,
                   decided_at: datetime) -> list[RunSourceComparison]:
    """Decide pointer promotion per comparison; only a completed run promotes eligible sources."""
    planned: list[RunSourceComparison] = []
    for comparison in comparisons:
        if run_status in TERMINAL_FAILURE:
            planned.append(comparison.model_copy(update={"promotion": "skipped", "promoted_at": None,
                                                         "decided_at": decided_at}))
        elif run_status == "completed" and comparison.outcome in PROMOTABLE_OUTCOMES:
            planned.append(comparison.model_copy(update={"promotion": "promoted", "promoted_at": decided_at,
                                                         "decided_at": decided_at}))
        elif run_status == "completed":
            planned.append(comparison.model_copy(update={"promotion": "skipped", "promoted_at": None,
                                                         "decided_at": decided_at}))
        else:
            planned.append(comparison.model_copy(update={"promotion": "pending", "decided_at": decided_at}))
    return planned


def pin_baselines(source_ids: list[str], baseline_map: dict[str, str | None]) -> dict[str, UUID | None]:
    """Build the frozen acceptance map; unknown sources pin to no baseline, never an invented one."""
    return {source_id: UUID(baseline_map[source_id]) if baseline_map.get(source_id) else None
            for source_id in source_ids}
