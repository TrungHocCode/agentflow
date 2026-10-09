"""CI live-loop hooks: capture every researcher fetch, plan rounds, brief on completion.

The hooks plug into RunService observers. They never call a model and never consume run
budget themselves; round tasks they return are budget-checked at acceptance and executed
by the existing dispatcher/worker path. Any hook failure is contained by the caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid5, NAMESPACE_URL

from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_contracts import InvestigationQuestion, RoundScope
from app.modules.competitive_intelligence.investigation_coordinator import coverage_status, should_terminate
from app.modules.competitive_intelligence.models import InvestigationBudget, WatchlistConfig, digest
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate
from app.modules.competitive_intelligence.snapshot_policy import promotion_plan
from app.shared.collection_scope import normalized_url

COLLECTION_ROLE = "source_researcher"


class CILoopHooks:
    """Domain observer for CI runs; no-ops on runs without frozen CI scope."""

    def __init__(self, snapshots, investigations, briefs, research, artifacts) -> None:
        self.snapshots, self.investigations, self.briefs = snapshots, investigations, briefs
        self.research, self.artifacts = research, artifacts

    @staticmethod
    def ci_scope(run) -> dict | None:
        scope = (run.input_data or {}).get("competitive_intelligence")
        return scope if isinstance(scope, dict) else None

    @staticmethod
    def _source_map(scope: dict) -> dict[str, dict]:
        """Approved URL (normalized) -> source identity, config version and context."""
        config = WatchlistConfig.model_validate(scope["config"])
        mapping: dict[str, dict] = {}
        for product in config.products:
            for source in product.sources:
                if not source.enabled or source.id is None:
                    continue
                mapping[normalized_url(source.url)] = {
                    "source_id": source.id, "url": source.url, "kind": source.kind,
                    "language": source.context.language, "region": source.context.region,
                    "config_version": digest(source),
                }
        return mapping

    @staticmethod
    def _questions(scope: dict, watchlist_id: UUID) -> list[InvestigationQuestion]:
        config = WatchlistConfig.model_validate(scope["config"])
        return [InvestigationQuestion(id=uuid5(NAMESPACE_URL, f"ci:question:{watchlist_id}:{criterion.field}"),
                                      text=f"{criterion.field}: {criterion.objective}",
                                      dimension=criterion.field[:64])
                for criterion in config.comparison_criteria]

    @staticmethod
    def _round_scope(scope: dict) -> RoundScope:
        config = WatchlistConfig.model_validate(scope["config"])
        identities = [source.id for product in config.products for source in product.sources
                      if source.enabled and source.id is not None]
        return RoundScope(approved_source_ids=identities, approved_urls=list(scope.get("approved_urls", [])),
                          config_hash=scope["config_hash"])

    @staticmethod
    def _spent(run) -> tuple[int, int, int]:
        """Return (spent_calls, spent_tokens, spent_tasks) for budget validation."""
        tasks = [task for task in (run.plan or []) if getattr(task, "status", None) != "pending"]
        budget = (run.metadata or {}).get("run_budget", {}) or {}
        return (int(budget.get("calls", 0) or 0), int(budget.get("reserved_estimated_tokens", 0) or 0),
                len(tasks))

    async def _source_texts(self, run_id: str, task_id: int) -> list[dict]:
        """Load durable source documents collected by one task with their stored text."""
        records = await self.research.list_results(run_id, limit=200)
        documents = []
        for record in records:
            metadata = record.metadata or {}
            if metadata.get("kind") != "source_document" or str(record.task_id) != str(task_id):
                continue
            content = record.content or {}
            uri, url = content.get("storage_uri"), content.get("source_url")
            if not uri or not url:
                continue
            text = self.artifacts.resolve(uri).read_text(encoding="utf-8")
            documents.append({"source_url": url, "text": text})
        return documents

    async def on_task_completed(self, run, task: Task, status: str) -> list[Task]:
        """Capture researcher fetches, compare, record, and append one accepted round at most."""
        scope = self.ci_scope(run)
        if scope is None or (task.agent_id or task.node) != COLLECTION_ROLE:
            return []
        frozen = scope.get("baselines", {}) or {}
        mapping = self._source_map(scope)
        changed: list[ChangeCandidate] = []
        for document in await self._source_texts(run.run_id, task.id):
            entry = mapping.get(normalized_url(document["source_url"]))
            if entry is None:
                continue
            now = datetime.now(timezone.utc)
            outcome, snapshot = await self.snapshots.capture(
                run_id=UUID(run.run_id), source_id=entry["source_id"],
                revision_id=UUID(scope["revision_id"]), owner_id=run.user_id,
                requested_url=entry["url"], final_url=document["source_url"], http_status=200,
                fetch_status="success", reason_codes=[], captured_text=document["text"],
                source_kind=entry["kind"], language=entry["language"], region=entry["region"],
                source_config_version=entry["config_version"], title=None, content_type=None,
                observed_at=now, fetched_at=now)
            if snapshot is None:
                continue
            comparison, candidates = await self.snapshots.compare_source(
                owner_id=run.user_id, run_id=UUID(run.run_id), source_id=entry["source_id"],
                current_snapshot_id=snapshot.id, decided_at=now)
            await self.snapshots.finalize_source(
                owner_id=run.user_id, comparison=comparison, candidates=candidates, run_status="running",
                decided_at=now, pinned_baseline_id=self._pinned(frozen, entry["source_id"]))
            if comparison.outcome == "changed":
                changed.extend(candidates)
        if not changed:
            return []
        budget = InvestigationBudget.model_validate(scope["config"]["budget"])
        questions = self._questions(scope, UUID(scope["watchlist_id"]))
        accepted = [round for round in await self.investigations.list_rounds(
            UUID(run.run_id), run.user_id) if round.status in ("accepted", "completed")]
        spent_calls, spent_tokens, spent_tasks = self._spent(run)
        if should_terminate(len(accepted) + 1, coverage_status(questions, accepted), budget,
                            spent_calls)[0]:
            return []
        stored, decision = await self.investigations.propose_round(
            UUID(run.run_id), UUID(scope["watchlist_id"]), UUID(scope["revision_id"]), run.user_id,
            questions, changed, self._round_scope(scope), budget, spent_calls, spent_tokens, spent_tasks,
            False, datetime.now(timezone.utc))
        if decision != "accepted":
            return []
        start_id = max([task.id for task in (run.plan or [])] + [0]) + 1
        return await self.investigations.materialize_tasks(stored, start_id, run.user_id)

    async def on_run_completed(self, run) -> None:
        """Promote eligible pointers by CAS, record final promotion states, then build the brief."""
        scope = self.ci_scope(run)
        if scope is None:
            return
        frozen = scope.get("baselines", {}) or {}
        comparisons = await self.snapshots.list_comparisons(run.run_id, run.user_id)
        for comparison in comparisons:
            planned = promotion_plan([comparison], "completed", datetime.now(timezone.utc))[0]
            if planned.promotion == "promoted" and comparison.current_snapshot_id is not None:
                current = await self.snapshots.get_snapshot(str(comparison.current_snapshot_id), run.user_id)
                state = await self.snapshots.promote(
                    current, run.run_id, self._pinned_str(frozen, comparison.source_id))
                final = planned.promotion if state == "promoted" else "rejected_stale"
            else:
                final = "skipped"
            await self.snapshots.set_promotion(run.run_id, str(comparison.source_id), run.user_id, final,
                                               datetime.now(timezone.utc))
        if comparisons:
            await self.briefs.build_brief(UUID(scope["watchlist_id"]), UUID(scope["revision_id"]),
                                          UUID(run.run_id), run.user_id, datetime.now(timezone.utc))

    @staticmethod
    def _pinned(frozen: dict, source_id: UUID) -> UUID | None:
        value = frozen.get(str(source_id))
        return UUID(value) if value else None

    @staticmethod
    def _pinned_str(frozen: dict, source_id: UUID) -> str | None:
        value = frozen.get(str(source_id))
        return str(value) if value else None
