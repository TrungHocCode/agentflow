"""CI-P6 fixed-domain scenario replay: golden fixtures through the real deterministic pipeline.

No network, no model, no clock dependence. Each scenario drives capture normalization,
quality gating, comparison, section diff, baseline policy, coordinator validation and
brief outcome derivation, then scores predicted vs annotated truth. The same fixtures
back the unit suite and the release evidence script.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from app.execution.state import Task
from app.modules.competitive_intelligence.investigation_contracts import (
    InvestigationQuestion, InvestigationRound, RoundScope, RoundTask,
)
from app.modules.competitive_intelligence.investigation_coordinator import (
    plan_round, reconstruct_after_restart, validate_round,
)
from app.modules.competitive_intelligence.models import InvestigationBudget
from app.modules.competitive_intelligence.snapshot_contracts import (
    EXTRACTOR_VERSION, NORMALIZATION_VERSION, FetchOutcome, SourceSnapshot,
)
from app.modules.competitive_intelligence.snapshot_normalize import (
    compare_snapshots, context_hash, detect_candidates, hash_text, normalize_captured, quality_gate,
)
from app.modules.competitive_intelligence.snapshot_policy import eligible_for_baseline

UNIT_OWNER = "evaluation"
FIXTURE_VERSION = "ci-scenarios-v1"


@dataclass
class ScenarioVerdict:
    scenario_id: str
    passed: bool
    outcome: str
    candidate_kinds: list[str]
    baseline_advances: bool
    round_outcome: str | None
    terminate_reason: str | None
    false_alerts: int
    elapsed_ms: float
    notes: list[str] = field(default_factory=list)


def _snapshot(source_id: UUID, run_id: UUID, text: str, context: str, config_version: str,
              quality: str, reasons: list[str], observed_at: datetime) -> tuple[SourceSnapshot, str]:
    normalized = normalize_captured(text, "pricing")
    outcome_id = uuid4()
    snapshot = SourceSnapshot(id=uuid4(), owner_id=UNIT_OWNER, source_id=source_id, run_id=run_id,
        fetch_outcome_id=outcome_id, requested_url="https://rival.invalid/pricing",
        final_url="https://rival.invalid/pricing", source_context_hash=context,
        source_config_version=config_version, extractor_version=EXTRACTOR_VERSION,
        normalization_version=NORMALIZATION_VERSION, captured_uri="", captured_hash=hash_text(text),
        normalized_uri="", normalized_hash=hash_text(normalized), title="Pricing",
        content_type="text/plain", quality=quality, quality_reason_codes=reasons, truncated=False,
        fetched_at=observed_at, observed_at=observed_at)
    return snapshot, normalized


def run_scenario(fixture: dict, observed_at: datetime) -> ScenarioVerdict:
    """Replay one golden fixture; returns predicted verdict scored against its truth later."""
    started = perf_counter()
    notes: list[str] = []
    truth = fixture["truth"]
    source_id, run_id, revision_id = uuid4(), uuid4(), uuid4()
    config_version = hash_text("config")
    context_before = context_hash("https://rival.invalid/pricing", "https://rival.invalid/pricing", "en",
                                  fixture.get("region_before", "US"))
    context_after = context_hash("https://rival.invalid/pricing", "https://rival.invalid/pricing", "en",
                                 fixture.get("region_after", fixture.get("region_before", "US")))
    fetch_status = fixture.get("fetch_status", "success")
    outcome_record = FetchOutcome(id=uuid4(), run_id=run_id, source_id=source_id, revision_id=revision_id,
        requested_url="https://rival.invalid/pricing", final_url="https://rival.invalid/pricing",
        http_status=200 if fetch_status == "success" else None, fetch_status=fetch_status,
        reason_codes=fixture.get("fetch_reasons", []), bytes_observed=len(fixture.get("after") or ""),
        truncated=False, snapshot_id=None, attempted_at=observed_at, observed_at=observed_at)
    if fetch_status != "success" or not fixture.get("after"):
        comparison_outcome, kinds, advances, round_outcome, terminate = "unavailable", [], False, None, None
    else:
        quality, reasons = quality_gate(fixture["after"], "https://rival.invalid/pricing", 200, False, "pricing")
        current, current_text = _snapshot(source_id, run_id, fixture["after"], context_after, config_version,
                                          quality, reasons, observed_at)
        outcome_record = outcome_record.model_copy(update={"snapshot_id": current.id})
        baseline, baseline_text = None, None
        if fixture.get("before") is not None:
            baseline, baseline_text = _snapshot(source_id, run_id, fixture["before"], context_before,
                                               config_version, "eligible", [], observed_at)
        comparison = compare_snapshots(current, current_text, baseline, baseline_text, run_id, observed_at)
        comparison_outcome = comparison.outcome
        kinds = []
        candidates = []
        if comparison_outcome == "changed" and baseline_text is not None:
            candidates = detect_candidates(baseline_text, current_text, run_id, source_id, baseline.id,
                                           current.id, observed_at)
            kinds = [candidate.kind for candidate in candidates]
        advances = eligible_for_baseline(current, outcome_record) and comparison_outcome in (
            "baseline_created", "no_change", "changed")
        round_outcome, terminate = None, None
        if fixture.get("round_check"):
            scope = RoundScope(approved_source_ids=[] if fixture["round_check"] == "reject_scope"
                               else [source_id], approved_urls=["https://rival.invalid/pricing"],
                               config_hash="c" * 64)
            budget = InvestigationBudget(**fixture.get("budget", {})) if fixture.get("budget") \
                else InvestigationBudget()
            questions = [InvestigationQuestion(id=uuid4(), text="Did pricing change?", dimension="pricing",
                                               candidate_ids=[c.id for c in candidates])]
            planned = plan_round(run_id, uuid4(), revision_id, 1, None, questions, candidates, scope,
                                 budget, False, observed_at)
            decision = validate_round(planned, scope, budget, [], fixture.get("spent_calls", 0), 0, 0,
                                      observed_at)
            round_outcome = decision.outcome
            if decision.outcome == "rejected":
                notes.extend(decision.reasons)
        if fixture.get("restart"):
            round_task = RoundTask(task_key="r1-research", role="source_researcher", description="Confirm.",
                                   tool_names=["http_request"], source_ids=[source_id], estimated_calls=2)
            rounds = [InvestigationRound(id=uuid4(), run_id=run_id, watchlist_id=uuid4(),
                                         revision_id=revision_id, round_number=1, tasks=[round_task],
                                         scope_digest="s" * 64, reserved_calls=2, reserved_tokens=2,
                                         status="accepted", created_at=observed_at)]
            tasks = [Task(id=1, node="source_researcher", description="d", status="running",
                          config={"investigation_round_id": str(rounds[0].id)})]
            round_updates, task_updates = reconstruct_after_restart(rounds, tasks)
            terminate = task_updates[1][0] if 1 in task_updates else None
    elapsed_ms = (perf_counter() - started) * 1000
    predicted_kinds = sorted(kinds)
    truth_kinds = sorted(truth.get("candidate_kinds", []))
    false_alerts = len(predicted_kinds) - len([kind for kind in predicted_kinds if kind in truth_kinds])
    passed = (comparison_outcome == truth["comparison_outcome"] and predicted_kinds == truth_kinds
              and advances == truth.get("baseline_advances", False)
              and (truth.get("round_outcome") is None or round_outcome == truth["round_outcome"])
              and (truth.get("terminate_reason") is None or terminate == truth["terminate_reason"]))
    return ScenarioVerdict(scenario_id=fixture["id"], passed=passed, outcome=comparison_outcome,
                           candidate_kinds=predicted_kinds, baseline_advances=advances,
                           round_outcome=round_outcome, terminate_reason=terminate, false_alerts=false_alerts,
                           elapsed_ms=elapsed_ms, notes=notes)


def score_suite(fixtures: list[dict], observed_at: datetime) -> dict:
    """Run every fixture; aggregate precision/recall/false alerts over predicted candidates."""
    verdicts = [run_scenario(fixture, observed_at) for fixture in fixtures]
    predicted = sum(len(verdict.candidate_kinds) for verdict in verdicts)
    truth_total = sum(len(fixture["truth"].get("candidate_kinds", [])) for fixture in fixtures)
    matched = sum(1 for verdict, fixture in zip(verdicts, fixtures)
                  if sorted(verdict.candidate_kinds) == sorted(fixture["truth"].get("candidate_kinds", [])))
    precision = matched / len(verdicts) if verdicts else 1.0
    recall = (sum(min(len(v.candidate_kinds), len(f["truth"].get("candidate_kinds", [])))
                    for v, f in zip(verdicts, fixtures)) / truth_total) if truth_total else 1.0
    return {"fixture_version": FIXTURE_VERSION, "scenarios": len(verdicts),
            "passed": sum(1 for verdict in verdicts if verdict.passed),
            "failed": [verdict.scenario_id for verdict in verdicts if not verdict.passed],
            "candidate_precision_by_scenario": round(precision, 4),
            "candidate_recall": round(recall, 4),
            "false_alerts": sum(verdict.false_alerts for verdict in verdicts),
            "predicted_candidates": predicted, "truth_candidates": truth_total,
            "elapsed_ms": round(sum(verdict.elapsed_ms for verdict in verdicts), 2),
            "verdicts": [vars(verdict) for verdict in verdicts]}


def static_pipeline(fixtures: list[dict], observed_at: datetime) -> dict:
    """Hash-shortcut-only baseline: identical compare, but no rounds, coverage or verification."""
    static_kinds: list[list[str]] = []
    for fixture in fixtures:
        if fixture.get("fetch_status", "success") != "success" or not fixture.get("after"):
            static_kinds.append([])
            continue
        current_text = normalize_captured(fixture["after"], "pricing")
        quality, _ = quality_gate(fixture["after"], "https://rival.invalid/pricing", 200, False, "pricing")
        if quality != "eligible" or fixture.get("before") is None:
            static_kinds.append([])
            continue
        before_text = normalize_captured(fixture["before"], "pricing")
        if hash_text(before_text) == hash_text(current_text):
            static_kinds.append([])
            continue
        run_id, source_id, before_id, after_id = uuid4(), uuid4(), uuid4(), uuid4()
        static_kinds.append(sorted(candidate.kind for candidate in detect_candidates(
            before_text, current_text, run_id, source_id, before_id, after_id, observed_at)))
    truth_kinds = [sorted(fixture["truth"].get("candidate_kinds", [])) for fixture in fixtures]
    matched = sum(1 for predicted, truth in zip(static_kinds, truth_kinds) if predicted == truth)
    return {"matched_scenarios": matched, "total": len(fixtures),
            "note": "Static detection matches section diff; it provides no rounds, question coverage, "
                    "verification tasks or budget governance."}


def load_fixtures(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scenarios"]
