"""CI-P4 investigation use cases: plan rounds, accept them atomically, finalize and recover.

The service composes the code-owned coordinator with durable round storage. It never calls
a model; round tasks materialize through the existing runtime Task contract so the
dispatcher executes them unchanged.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_contracts import (
    InvestigationQuestion, InvestigationRound, RoundScope,
)
from app.modules.competitive_intelligence.investigation_coordinator import (
    coverage_status, plan_round, reconstruct_after_restart, scope_digest, to_runtime_tasks, validate_round,
)
from app.modules.competitive_intelligence.models import InvestigationBudget
from app.modules.competitive_intelligence.ports import InvestigationRepository, SnapshotRepository
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate, MAX_SPAN_CHARS

COORDINATOR_IDENTITY = "coordinator_code"


class InvestigationService:
    def __init__(self, rounds: InvestigationRepository, snapshots: SnapshotRepository) -> None:
        self.rounds, self.snapshots = rounds, snapshots

    async def propose_round(self, run_id: UUID, watchlist_id: UUID, revision_id: UUID, owner_id: str,
                            questions: list[InvestigationQuestion], candidates: list[ChangeCandidate],
                            scope: RoundScope, budget: InvestigationBudget, spent_calls: int,
                            spent_tokens: int, spent_tasks: int, final: bool,
                            created_at: datetime) -> tuple[InvestigationRound, str]:
        """Plan, validate and persist one round; returns the stored round and its decision outcome."""
        history = await self.rounds.list_rounds(str(run_id), owner_id)
        accepted = [round for round in history if round.status in ("accepted", "completed")]
        taken = {round.round_number for round in history}
        latest = max([round.round_number for round in accepted], default=0)
        parent = next((round.id for round in accepted if round.round_number == latest), None)
        planned = plan_round(run_id, watchlist_id, revision_id, latest + 1, parent, questions, candidates,
                             scope, budget, final, created_at)
        if planned.round_number in taken:
            # A decided round already owns this number; report visibly without persisting a duplicate.
            return planned.model_copy(update={"status": "rejected", "decided_at": created_at,
                                              "decided_by": COORDINATOR_IDENTITY,
                                              "rejection_reasons": ["duplicate_round_number"]}), "rejected"
        decision = validate_round(planned, scope, budget, accepted, spent_calls, spent_tokens, spent_tasks,
                                  created_at)
        if decision.outcome == "accepted":
            stored = await self.rounds.save_round(
                planned.model_copy(update={"status": "accepted", "decided_at": created_at,
                                           "decided_by": COORDINATOR_IDENTITY}), owner_id)
        else:
            stored = await self.rounds.save_round(
                planned.model_copy(update={"status": "rejected", "decided_at": created_at,
                                           "decided_by": COORDINATOR_IDENTITY,
                                           "rejection_reasons": decision.reasons}), owner_id)
        return stored, decision.outcome

    async def materialize_tasks(self, round: InvestigationRound, start_id: int, owner_id: str,
                                default_timeout_seconds: int | None = None) -> list[Task]:
        """Resolve scoped snapshot excerpts (owner-checked) into runtime task inputs."""
        excerpts: dict[str, str] = {}
        for task in round.tasks:
            for identity in [*task.snapshot_ids, *task.candidate_ids]:
                key = str(identity)
                if key not in excerpts:
                    try:
                        snapshot = await self.snapshots.get_snapshot(key, owner_id)
                        excerpts[key] = await self.snapshots.read_snapshot_span(
                            key, owner_id, "normalized", 0, MAX_SPAN_CHARS)
                    except Exception:
                        excerpts[key] = ""
        return to_runtime_tasks(round, start_id, excerpts, default_timeout_seconds)

    async def finalize_round(self, round_id: str, owner_id: str, plan_tasks: list[Task],
                             decided_at: datetime) -> InvestigationRound:
        """Mark an accepted round completed once every materialized task is terminal."""
        round = await self.rounds.get_round(round_id, owner_id)
        if round.status != "accepted":
            raise ValueError("Only an accepted round can be finalized.")
        round_task_ids = {task.task_key for task in round.tasks}
        states = {task.status for task in plan_tasks if (task.config or {}).get("investigation_round_id")
                  == round_id or task.task_key in round_task_ids}
        if not states or not states <= {"done", "partial", "failed", "skipped", "interrupted"}:
            raise ValueError("Round tasks are not all terminal.")
        return await self.rounds.set_round_status(round_id, owner_id, "completed", COORDINATOR_IDENTITY)

    async def reconstruct(self, run_id: UUID, owner_id: str,
                          plan_tasks: list[Task]) -> tuple[dict[str, str], dict[int, tuple[str, str]]]:
        """Reload accepted rounds and mark uncertain running attempts interrupted without replay."""
        rounds = await self.rounds.list_rounds(str(run_id), owner_id)
        round_updates, task_updates = reconstruct_after_restart(rounds, plan_tasks)
        for round_id, status in round_updates.items():
            await self.rounds.set_round_status(round_id, owner_id, status, COORDINATOR_IDENTITY)
        return round_updates, task_updates

    @staticmethod
    def coverage(questions: list[InvestigationQuestion],
                 accepted_rounds: list[InvestigationRound]) -> list:
        return coverage_status(questions, accepted_rounds)

    @staticmethod
    def expected_scope_digest(scope: RoundScope) -> str:
        return scope_digest(scope)
