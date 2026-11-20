"""CI live-loop hooks: capture every researcher fetch, plan rounds, brief on completion.

The hooks plug into RunService observers. They never call a model and never consume run
budget themselves; round tasks they return are budget-checked at acceptance and executed
by the existing dispatcher/worker path. Any hook failure is contained by the caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from app.execution.agents.resolver import DEFAULT_AGENT_PROFILES
from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_contracts import (
    CoverageEntry, InvestigationQuestion, RoundScope,
)
from app.modules.competitive_intelligence.investigation_coordinator import coverage_status, should_terminate
from app.modules.competitive_intelligence.investigation_service import COORDINATOR_IDENTITY, change_text
from app.modules.competitive_intelligence.models import InvestigationBudget, WatchlistConfig, digest
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate
from app.modules.competitive_intelligence.snapshot_policy import promotion_plan
from app.shared.collection_scope import normalized_url, same_site


CHANGE_EXCERPT_CHARS = 600


class CILoopHooks:
    """Domain observer for CI runs; no-ops on runs without frozen CI scope."""

    def __init__(self, snapshots, investigations, briefs, research, artifacts,
                 agent_profiles=None, notifications=None, decider=None) -> None:
        self.snapshots, self.investigations, self.briefs = snapshots, investigations, briefs
        self.research, self.artifacts = research, artifacts
        self.agent_profiles = agent_profiles
        self.notifications = notifications
        # None keeps today's code-planned rounds; a SupervisorDecider lets the Supervisor choose.
        self.decider = decider

    async def _collects_evidence(self, task: Task) -> bool:
        """Whether the task's agent collects source pages. Workflow-seeded tasks may carry a
        catalog UUID in agent_id/node, while round tasks carry the profile name."""
        identifier = str(task.agent_id or task.node or "")
        if not identifier:
            return False
        profile = await self.agent_profiles.get_agent(identifier) if self.agent_profiles is not None else None
        profile = profile or DEFAULT_AGENT_PROFILES.get(identifier)
        return profile is not None and profile.mode == "collect_evidence"

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
    def _questions(scope: dict, watchlist_id: UUID,
                   changed: list[ChangeCandidate]) -> list[InvestigationQuestion]:
        """Criteria are shared across products, so every observed change bears on every criterion.

        This link is what brings the analyst into a round and lets coverage complete.
        """
        config = WatchlistConfig.model_validate(scope["config"])
        candidate_ids = [candidate.id for candidate in changed][:50]
        return [InvestigationQuestion(id=uuid5(NAMESPACE_URL, f"ci:question:{watchlist_id}:{criterion.field}"),
                                      text=f"{criterion.field}: {criterion.objective}",
                                      dimension=criterion.field[:64], candidate_ids=candidate_ids)
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

    @staticmethod
    def _same_site_entry(mapping: dict[str, dict], source_url: str) -> dict | None:
        """Match redirect-resolved hosts (e.g. www.) back to their approved entry."""
        from urllib.parse import urlsplit
        host = (urlsplit(source_url).hostname or "").lower()
        for approved_url, entry in mapping.items():
            approved_host = (urlsplit(approved_url).hostname or "").lower()
            if host and approved_host and same_site(host, approved_host):
                return entry
        return None

    async def on_task_completed(self, run, task: Task, status: str) -> list[Task]:
        """Capture researcher fetches, compare, record, and append one accepted round at most."""
        scope = self.ci_scope(run)
        if scope is None:
            return []
        # A run holds one comparison per source, made from its initial collection. Round
        # researchers confirm that change; capturing again would conflict with it.
        if (task.config or {}).get("investigation_round_id"):
            return await self._after_round(run, scope, task) if self.decider is not None else []
        if not await self._collects_evidence(task):
            return []
        frozen = scope.get("baselines", {}) or {}
        mapping = self._source_map(scope)
        changed: list[ChangeCandidate] = []
        for document in await self._source_texts(run.run_id, task.id):
            entry = mapping.get(normalized_url(document["source_url"]))
            if entry is None:
                entry = self._same_site_entry(mapping, document["source_url"])
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
        questions = self._questions(scope, UUID(scope["watchlist_id"]), changed)
        accepted = [round for round in await self.investigations.list_rounds(
            UUID(run.run_id), run.user_id) if round.status in ("accepted", "completed")]
        spent_calls, spent_tokens, spent_tasks = self._spent(run)
        if should_terminate(len(accepted) + 1, coverage_status(questions, accepted), budget,
                            spent_calls)[0]:
            return []
        return await self._plan_and_materialize(run, scope, budget, questions, changed, accepted,
                                                spent_calls, spent_tokens, spent_tasks)

    async def _plan_and_materialize(self, run, scope: dict, budget: InvestigationBudget,
                                    questions: list[InvestigationQuestion], changed: list[ChangeCandidate],
                                    accepted: list, spent_calls: int, spent_tokens: int, spent_tasks: int,
                                    end_of_round: bool = False) -> list[Task]:
        """One decision point. The Supervisor chooses when enabled; any failure runs the default plan,
        except at end of round where the default is to stop (the changes were already analyzed)."""
        planner, candidates = COORDINATOR_IDENTITY, changed
        if self.decider is not None:
            choice = await self._supervisor_choice(run, scope, changed, accepted, budget, spent_calls)
            if choice is None and end_of_round:
                return []
            if choice is not None:
                if choice[0] is None:
                    return []
                planner, candidates = "supervisor", choice[0]
        args = (UUID(run.run_id), UUID(scope["watchlist_id"]), UUID(scope["revision_id"]), run.user_id,
                questions, candidates, self._round_scope(scope), budget, spent_calls, spent_tokens, spent_tasks,
                False, datetime.now(timezone.utc))
        stored, decision = await self.investigations.propose_round(*args, planner)
        if decision != "accepted" and planner != COORDINATOR_IDENTITY:
            self._record(run, {"action": "rejected_by_validation", "targets": []})
            if end_of_round:
                return []
            args = (*args[:5], changed, *args[6:])
            stored, decision = await self.investigations.propose_round(*args, COORDINATOR_IDENTITY)
        if decision != "accepted":
            return []
        start_id = max([task.id for task in (run.plan or [])] + [0]) + 1
        return await self.investigations.materialize_tasks(stored, start_id, run.user_id)

    @staticmethod
    def _record(run, entry: dict) -> None:
        """Audit trail on the run document: every Supervisor decision and pending question."""
        run.metadata.setdefault("supervisor", {}).setdefault("decisions", []).append(entry)

    async def _supervisor_choice(self, run, scope: dict, changed: list[ChangeCandidate], accepted: list,
                                 budget: InvestigationBudget,
                                 spent_calls: int) -> tuple[list[ChangeCandidate] | None] | None:
        """Ask the Supervisor. None = use the default; (None,) = no round; ([...],) = round on these."""
        config = WatchlistConfig.model_validate(scope["config"])
        analyzed = {str(source_id) for round in accepted for task in round.tasks
                    if task.role == "competitive_analyst" for source_id in task.source_ids}
        by_source: dict[str, list[ChangeCandidate]] = {}
        for candidate in changed:
            by_source.setdefault(str(candidate.source_id), []).append(candidate)
        try:
            comparisons = {str(item.source_id): item.outcome
                           for item in await self.snapshots.list_comparisons(run.run_id, run.user_id)}
            labels: dict[str, str] = {}
            sources: list[dict] = []
            for product in config.products:
                for source in product.sources:
                    if not source.enabled or source.id is None or str(source.id) not in comparisons:
                        continue
                    label = f"S{len(labels) + 1}"
                    labels[label] = str(source.id)
                    item = {"id": label, "product": product.name, "dimension": source.kind,
                            "status": comparisons[str(source.id)], "analyzed": str(source.id) in analyzed}
                    if str(source.id) in by_source:
                        text = "\n".join(change_text(candidate) for candidate in by_source[str(source.id)])
                        item["change"] = text[:CHANGE_EXCERPT_CHARS]
                    sources.append(item)
            own = next((product for product in config.products if product.kind == "own"), None)
            raw = {"goal": config.goal, "round": len(accepted) + 1,
                   "budget_calls_left": max(0, budget.max_llm_calls - spent_calls), "sources": sources,
                   "actions_taken": [f"analyze_change {label}" for label, identity in labels.items()
                                     if identity in analyzed]}
            if own is not None:
                raw["own_product"] = {"name": own.name, "description": own.description[:300]}
            decision = await self.decider.decide(raw)
        except Exception:  # the Supervisor can never make a run worse: fall back to the default plan
            return None
        if decision is None:
            return None
        self._record(run, {"round": raw["round"], "action": decision.action, "targets": decision.targets,
                           "situation": decision.situation[:300], "reason": decision.reason[:300]})
        if decision.action == "ask_user":
            # The question is the model's reason field; pending until the user sees it on the run.
            run.metadata["supervisor"].setdefault("pending_questions", []).append(decision.reason[:300])
        if decision.action != "analyze_change":
            return (None,)
        wanted = {labels[target] for target in decision.targets}
        return ([candidate for candidate in changed if str(candidate.source_id) in wanted],)

    async def _after_round(self, run, scope: dict, task: Task) -> list[Task]:
        """End of round: when every task of the round is terminal, one more decision point (once per round)."""
        round_id = task.config["investigation_round_id"]
        state = run.metadata.setdefault("supervisor", {})
        siblings = [other for other in (run.plan or []) if (other.config or {}).get("investigation_round_id")
                    == round_id and other.id != task.id]
        if round_id in state.get("rounds_closed", []) or any(
                other.status not in ("done", "partial", "failed", "skipped", "interrupted") for other in siblings):
            return []
        state.setdefault("rounds_closed", []).append(round_id)
        budget = InvestigationBudget.model_validate(scope["config"]["budget"])
        accepted = [round for round in await self.investigations.list_rounds(
            UUID(run.run_id), run.user_id) if round.status in ("accepted", "completed")]
        spent_calls, spent_tokens, spent_tasks = self._spent(run)
        # Unanalyzed changes count as uncovered work; the round and call budgets stay the hard stop.
        if should_terminate(len(accepted) + 1, [CoverageEntry(question_id=uuid4(), status="uncovered")],
                            budget, spent_calls)[0]:
            return []
        changed = await self.snapshots.list_candidates(run.run_id, run.user_id)
        if not changed:
            return []
        questions = self._questions(scope, UUID(scope["watchlist_id"]), changed)
        return await self._plan_and_materialize(run, scope, budget, questions, changed, accepted,
                                                spent_calls, spent_tokens, spent_tasks, end_of_round=True)

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

    async def on_run_failed(self, run) -> None:
        """Tell the owner when a CI run did not finish; scheduled runs have no one watching."""
        scope = self.ci_scope(run)
        if scope is None or self.notifications is None:
            return
        await self.notifications.notify(run.user_id, "run_failed", "A watchlist run did not finish.",
                                        (run.error_message or "Open the run for details.")[:1000],
                                        scope.get("watchlist_id"), run.run_id)

    @staticmethod
    def _pinned(frozen: dict, source_id: UUID) -> UUID | None:
        value = frozen.get(str(source_id))
        return UUID(value) if value else None

    @staticmethod
    def _pinned_str(frozen: dict, source_id: UUID) -> str | None:
        value = frozen.get(str(source_id))
        return str(value) if value else None
