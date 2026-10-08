"""Code-owned investigation coordinator: plans bounded rounds, validates them, tracks coverage.

The coordinator never calls a model. It turns changed candidates and uncovered questions into
scoped round tasks, rejects out-of-scope/over-budget/cyclic proposals, and reconstructs
round state after a restart without replaying uncertain attempts.
"""

from datetime import datetime
from uuid import UUID, uuid4

from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_contracts import (
    CI_ROLE_TOOLS, MAX_ROUND_TASKS, ROUND_VERSION, CoverageEntry, InvestigationQuestion, InvestigationRound,
    RoundDecision, RoundScope, RoundTask,
)
from app.modules.competitive_intelligence.models import InvestigationBudget, digest
from app.modules.competitive_intelligence.snapshot_contracts import ChangeCandidate

TOKENS_PER_CALL_ESTIMATE = 4096
TERMINAL_ROUND_TASKS = ("done", "partial")


def scope_digest(scope: RoundScope) -> str:
    return digest({"approved_source_ids": sorted(str(identity) for identity in scope.approved_source_ids),
                   "approved_urls": sorted(scope.approved_urls), "config_hash": scope.config_hash})


def _role_tasks(source_label: str, candidate: ChangeCandidate, round_number: int) -> list[RoundTask]:
    researcher_key = f"r{round_number}-research-{candidate.id.hex[:8]}"
    verifier_key = f"r{round_number}-verify-{candidate.id.hex[:8]}"
    return [
        RoundTask(task_key=researcher_key, role="source_researcher",
            description=f"Confirm the observed change in section '{candidate.section or 'preamble'}' "
                        f"against the approved source; preserve provenance and distinguish source text "
                        f"from interpretation. Scope: source {candidate.source_id}.",
            tool_names=list(CI_ROLE_TOOLS["source_researcher"]), source_ids=[candidate.source_id],
            snapshot_ids=[candidate.before_snapshot_id, candidate.after_snapshot_id],
            candidate_ids=[candidate.id], estimated_calls=6),
        RoundTask(task_key=verifier_key, role="evidence_verifier",
            description=f"Examine supporting and contradictory evidence for candidate {candidate.id} "
                        f"({candidate.kind} in '{candidate.section or 'preamble'}'); label each claim "
                        f"observed or unresolved with exact snapshot offsets. Scope: source {candidate.source_id}.",
            tool_names=list(CI_ROLE_TOOLS["evidence_verifier"]), source_ids=[candidate.source_id],
            snapshot_ids=[candidate.before_snapshot_id, candidate.after_snapshot_id],
            candidate_ids=[candidate.id], depends_on_keys=[researcher_key], max_iterations=2,
            estimated_calls=2),
    ]


def plan_round(run_id: UUID, watchlist_id: UUID, revision_id: UUID, round_number: int,
               parent_round_id: UUID | None, questions: list[InvestigationQuestion],
               candidates: list[ChangeCandidate], scope: RoundScope, budget: InvestigationBudget,
               final: bool, created_at: datetime) -> InvestigationRound:
    """Build one bounded round from changed candidates; analysts join only where questions need them."""
    ordered = sorted(candidates, key=lambda candidate: (str(candidate.source_id), candidate.section or "",
                                                        candidate.detected_at.isoformat()))
    tasks: list[RoundTask] = []
    analyst_sources: dict[str, list[UUID]] = {}
    for candidate in ordered:
        if len(tasks) + 2 > MAX_ROUND_TASKS:
            break
        tasks.extend(_role_tasks(str(candidate.source_id), candidate, round_number))
        for question in questions:
            if candidate.id in question.candidate_ids:
                analyst_sources.setdefault(str(candidate.source_id), []).append(question.id)
    for source_id, question_ids in sorted(analyst_sources.items()):
        if len(tasks) + 1 > MAX_ROUND_TASKS:
            break
        analyst_key = f"r{round_number}-analyze-{source_id[:8]}"
        researcher_keys = [task.task_key for task in tasks
                           if task.role == "source_researcher" and str(task.source_ids[0]) == source_id]
        tasks.append(RoundTask(task_key=analyst_key, role="competitive_analyst",
            description=f"Compare the confirmed source observations for {source_id} with the own-product "
                        f"profile and the recorded customer segments and comparison criteria.",
            tool_names=list(CI_ROLE_TOOLS["competitive_analyst"]), source_ids=[UUID(source_id)],
            question_ids=sorted(set(question_ids)), depends_on_keys=researcher_keys, max_iterations=4,
            estimated_calls=4))
    if final:
        report_key = f"r{round_number}-report"
        tasks.append(RoundTask(task_key=report_key, role="report_agent",
            description="Present the validated round analysis; deterministic rendering owns files.",
            tool_names=list(CI_ROLE_TOOLS["report_agent"]),
            depends_on_keys=[task.task_key for task in tasks], max_iterations=3, estimated_calls=3))
    reserved_calls = sum(task.estimated_calls for task in tasks)
    if not tasks:
        raise ValueError("A round with no tasks answers nothing; terminate instead of planning it.")
    return InvestigationRound(id=uuid4(), run_id=run_id, watchlist_id=watchlist_id, revision_id=revision_id,
        round_number=round_number, parent_round_id=parent_round_id, tasks=tasks,
        scope_digest=scope_digest(scope), reserved_calls=reserved_calls,
        reserved_tokens=reserved_calls * TOKENS_PER_CALL_ESTIMATE, created_at=created_at)


def _acyclic(tasks: list[RoundTask]) -> bool:
    graph = {task.task_key: set(task.depends_on_keys) for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visited:
            return True
        if node in visiting:
            return False
        visiting.add(node)
        if not all(visit(parent) for parent in graph[node]):
            return False
        visiting.discard(node)
        visited.add(node)
        return True

    return all(visit(node) for node in graph)


def validate_round(round: InvestigationRound, scope: RoundScope, budget: InvestigationBudget,
                   accepted_rounds: list[InvestigationRound], spent_calls: int, spent_tokens: int,
                   spent_tasks: int, decided_at: datetime) -> RoundDecision:
    """Accept only scoped, budgeted, acyclic rounds that continue the accepted lineage."""
    reasons: list[str] = []
    approved = {str(identity) for identity in scope.approved_source_ids}
    if round.scope_digest != scope_digest(scope):
        reasons.append("scope_changed_since_acceptance")
    accepted_numbers = {accepted.round_number for accepted in accepted_rounds}
    if round.round_number in accepted_numbers:
        reasons.append("duplicate_round_number")
    latest = max(accepted_numbers) if accepted_numbers else 0
    if round.round_number != latest + 1:
        reasons.append("round_number_must_advance_lineage_by_one")
    if round.parent_round_id is not None and not any(
            accepted.id == round.parent_round_id and accepted.round_number == latest
            for accepted in accepted_rounds) and latest:
        reasons.append("parent_round_is_not_latest_accepted")
    if round.round_number > budget.max_rounds:
        reasons.append("round_budget_exhausted")
    if len(round.tasks) + spent_tasks > budget.max_tasks:
        reasons.append("task_budget_exhausted")
    if round.reserved_calls + spent_calls > budget.max_llm_calls:
        reasons.append("call_budget_exhausted")
    if round.reserved_tokens + spent_tokens > budget.max_total_tokens:
        reasons.append("token_budget_exhausted")
    if not _acyclic(round.tasks):
        reasons.append("round_tasks_contain_a_cycle")
    for task in round.tasks:
        if task.role not in CI_ROLE_TOOLS:
            reasons.append(f"role_not_approved:{task.role}")
            continue
        outside = [name for name in task.tool_names if name not in CI_ROLE_TOOLS[task.role]]
        if outside:
            reasons.append(f"tools_not_approved:{task.task_key}={','.join(sorted(outside))}")
        unknown_sources = [str(identity) for identity in task.source_ids if str(identity) not in approved]
        if unknown_sources:
            reasons.append(f"source_out_of_scope:{task.task_key}={','.join(sorted(unknown_sources))}")
        if task.role == "evidence_verifier" and not task.candidate_ids and not task.snapshot_ids:
            reasons.append(f"verifier_without_evidence_refs:{task.task_key}")
    if reasons:
        return RoundDecision(outcome="rejected", reasons=sorted(set(reasons)), decided_at=decided_at)
    return RoundDecision(outcome="accepted", reasons=[], decided_at=decided_at)


def coverage_status(questions: list[InvestigationQuestion],
                    accepted_rounds: list[InvestigationRound]) -> list[CoverageEntry]:
    """A question is covered only when an accepted round task references it or its candidates."""
    refs: dict[str, set[int]] = {}
    for round in accepted_rounds:
        for task in round.tasks:
            for question_id in task.question_ids:
                refs.setdefault(str(question_id), set()).add(round.round_number)
            for candidate_id in task.candidate_ids:
                for question in questions:
                    if candidate_id in question.candidate_ids:
                        refs.setdefault(str(question.id), set()).add(round.round_number)
    return [CoverageEntry(question_id=question.id,
                          status="covered" if str(question.id) in refs else "uncovered",
                          round_numbers=sorted(refs.get(str(question.id), set())))
            for question in questions]


def should_terminate(round_number: int, coverage: list[CoverageEntry], budget: InvestigationBudget,
                     spent_calls: int) -> tuple[bool, str]:
    if all(entry.status == "covered" for entry in coverage):
        return True, "coverage_complete"
    if round_number > budget.max_rounds:
        return True, "round_budget_exhausted"
    if spent_calls >= budget.max_llm_calls:
        return True, "call_budget_exhausted"
    return False, "investigation_continues"


def round_fits_budget(budget_snapshot: dict[str, int], round: InvestigationRound) -> bool:
    """Headroom check against a RunBudget snapshot; never consumes, so workers are not double-charged."""
    return (budget_snapshot.get("calls", 0) + round.reserved_calls <= budget_snapshot.get("max_calls", 0)
            and budget_snapshot.get("reserved_estimated_tokens", 0) + round.reserved_tokens
            <= budget_snapshot.get("max_tokens", 0))


def to_runtime_tasks(round: InvestigationRound, start_id: int, evidence_excerpts: dict[str, str],
                     default_timeout_seconds: int | None = None) -> list[Task]:
    """Materialize round tasks as runtime Tasks with lineage refs in config; ids follow the live plan."""
    runtime: list[Task] = []
    keys = [task.task_key for task in round.tasks]
    for offset, task in enumerate(round.tasks):
        references = [evidence_excerpts.get(str(identity), "") for identity in
                      [*task.snapshot_ids, *task.candidate_ids]]
        runtime.append(Task(id=start_id + offset, task_key=task.task_key, node=task.role, agent_id=task.role,
            tool_names=list(task.tool_names), status="pending", description=task.description,
            dependencies=[start_id + keys.index(key) for key in task.depends_on_keys],
            timeout_seconds=task.timeout_seconds or default_timeout_seconds,
            max_iterations=task.max_iterations,
            input_mapping={"investigation_round_id": str(round.id), "round_number": round.round_number,
                           "snapshot_ids": [str(identity) for identity in task.snapshot_ids],
                           "candidate_ids": [str(identity) for identity in task.candidate_ids],
                           "question_ids": [str(identity) for identity in task.question_ids],
                           "evidence_excerpts": [text for text in references if text][:8],
                           "round_version": ROUND_VERSION},
            config={"investigation_round_id": str(round.id), "round_number": round.round_number,
                    "round_version": ROUND_VERSION}))
    return runtime


def reconstruct_after_restart(rounds: list[InvestigationRound],
                              plan_tasks: list[Task]) -> tuple[dict[str, str], dict[int, tuple[str, str]]]:
    """Rebuild round/decision state without replaying uncertain attempts.

    Returns (round_id -> status, task_id -> (status, error)). Running tasks of accepted
    rounds become interrupted; accepted rounds with no remaining work complete.
    """
    accepted = {str(round.id): round for round in rounds if round.status == "accepted"}
    round_of = {task.id: (task.config or {}).get("investigation_round_id") for task in plan_tasks}
    task_updates: dict[int, tuple[str, str]] = {}
    for task in plan_tasks:
        if task.status == "running" and round_of.get(task.id) in accepted:
            task_updates[task.id] = ("interrupted", "restart_interrupted")
    round_updates: dict[str, str] = {}
    for round_id in accepted:
        round_tasks = [task for task in plan_tasks if round_of.get(task.id) == round_id]
        if not round_tasks:
            continue
        states = {task_updates[task.id][0] if task.id in task_updates else task.status
                  for task in round_tasks}
        if states <= {*TERMINAL_ROUND_TASKS, "failed", "skipped", "interrupted"}:
            round_updates[round_id] = "completed"
    return round_updates, task_updates
