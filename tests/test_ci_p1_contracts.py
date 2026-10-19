"""CI-P1 core contracts; mocked inference, isolated files, no development DB."""

import asyncio
import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.execution.agents.base import WorkerAgent
from app.execution.context_budget import ContextBudgetExceeded, guard_structured_context
from app.execution.research_contracts import (
    ChunkExtraction, EvidenceClaim, ExtractedClaim, ResearchRequirement, SynthesisFinding, SynthesisResult,
)
from app.execution.research_coverage import reconcile_coverage
from app.execution.research_evidence import EvidenceProcessor
from app.execution.research_presentation import EvidenceReport, render_evidence_report
from app.execution.research_reduction import EvidenceReducer
from app.execution.research_validation import source_spans, validate_candidate
from app.execution.run_budget import (
    MAX_ACTIVE_CHECK_INTERVAL_SECONDS, RunBudget, RunBudgetExceeded, RunNoLongerActive, bounded_invoke,
    bounded_operation, run_budget_scope,
)
from app.execution.state import Task
from app.execution.tools.contracts import SourceMetadata, success_result, failure_result
from app.execution.tools.markdown_report_generator_tool import markdown_report_generator
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.infrastructure.artifacts.storage import LocalArtifactStorage
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.modules.workflows.contract import normalize_workflow_definition
from app.shared.artifact_paths import artifact_scope
from test_support import isolated_workspace, use_test_adapters


def claim(identity: str = "e1", subject: str = "Alpha") -> EvidenceClaim:
    excerpt = f"{subject} context length is 262,144 tokens; use <think>."
    return EvidenceClaim(evidence_id=identity, document_id="d1", chunk_id="c1",
                         source_url="https://fixture.invalid/docs", claim=excerpt, excerpt=excerpt,
                         start_offset=0, end_offset=len(excerpt), subject=subject,
                         metric="context length", value_text="262,144", unit="tokens")


class _FakeClock:
    """Deterministic replacement for the ``monotonic`` callable used by run budgets.

    Tests advance it explicitly, so active-check throttling assertions never depend on
    wall-clock scheduling.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.start = start
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestCIPhaseOneContracts(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        use_test_adapters(self)
        self.workspace = self.enterContext(isolated_workspace())

    def test_requirements_reconcile_across_sources_and_do_not_invent_absence(self) -> None:
        requirements = [ResearchRequirement(id="a-context", subject="Alpha", field="context length"),
                        ResearchRequirement(id="b-context", subject="Beta", field="context length")]
        rows = reconcile_coverage(requirements, [claim()])
        self.assertEqual([row.status for row in rows], ["observed", "unresolved"])
        self.assertEqual(rows[0].evidence_ids, ["e1"])
        rows = reconcile_coverage(requirements, [claim(), claim("e2", "Beta")])
        self.assertTrue(all(row.status == "observed" for row in rows))

    def test_coverage_requires_explicit_alias_and_rejects_conflicting_ids(self) -> None:
        requirement = ResearchRequirement(id="context", subject="Alpha 4B", field="context")
        self.assertEqual(reconcile_coverage([requirement], [claim()])[0].status, "unresolved")
        requirement.subject_aliases = ["Alpha"]
        requirement.field_aliases = ["context length"]
        self.assertEqual(reconcile_coverage([requirement], [claim()])[0].status, "observed")
        with self.assertRaises(ValueError):
            reconcile_coverage([requirement, ResearchRequirement(id="context", subject="Beta", field="context")], [])

    def test_workflow_projection_preserves_required_subject_fields(self) -> None:
        requirement = ResearchRequirement(id="context", subject="Alpha", field="context length")
        task = Task(id=1, node="source_researcher", status="pending", description="Collect",
                    research_requirements=[requirement])
        normalized = normalize_workflow_definition({"tasks": [task.model_dump()]})
        self.assertEqual(normalized["tasks"][0]["research_requirements"], [requirement.model_dump()])
        self.assertEqual(normalize_workflow_definition(normalized)["tasks"], normalized["tasks"])

    def test_schema_size_and_unsupported_qualifiers_are_rejected(self) -> None:
        with self.assertRaises(ContextBudgetExceeded):
            guard_structured_context([HumanMessage(content="small")], {"description": "x" * 30000})
        excerpt = "Alpha context length is 262,144 tokens, setup A."
        for candidate, reason in [
            (ExtractedClaim(claim=excerpt, excerpt=excerpt, metric="price"), "metric_not_found"),
            (ExtractedClaim(claim=excerpt, excerpt=excerpt, evaluation_setup="setup B"), "setup_not_found"),
        ]:
            self.assertEqual(validate_candidate(candidate, excerpt, excerpt, source_spans(excerpt))[2], reason)

    def test_report_renders_exact_values_and_unresolved_coverage_deterministically(self) -> None:
        evidence = claim()
        coverage = reconcile_coverage([ResearchRequirement(id="beta", subject="Beta", field="price")], [evidence])
        report = EvidenceReport(title="Fixture", findings=[SynthesisFinding(text=evidence.claim, evidence_ids=["e1"])],
                                evidence=[evidence], requirement_coverage=coverage)
        text = render_evidence_report(report)
        self.assertEqual(text, render_evidence_report(report))
        self.assertIn("262,144", text)
        self.assertIn("<think>", text)
        self.assertIn("unresolved", text)
        self.assertIn("does not mean the publisher omitted", text)
        self.assertIn(evidence.source_url, text)
        self.assertIn("quote_matched", text)

    def test_report_rejects_unknown_lineage_altered_literals_and_verification_labels(self) -> None:
        for finding in [{"text": "Wrong", "evidence_ids": ["invented"]},
                        {"text": "Use <tool_call>", "evidence_ids": ["e1"]},
                        {"text": "Score 99.9%", "evidence_ids": ["e1"]},
                        {"text": "Verified", "evidence_ids": ["e1"], "verification": "supported"}]:
            with self.assertRaises(ValidationError):
                EvidenceReport(title="Fixture", findings=[finding], evidence=[claim()])

    async def test_failed_metadata_write_retains_staging_for_diagnosis(self) -> None:
        output = markdown_report_generator.invoke({"title": "Fixture", "filename": "fixture.md",
            "sections": [{"header": "Evidence", "content": "Fixture"}]})
        path = Path(json.loads(output)["data"]["file_path"])
        repository = MagicMock(save_result=AsyncMock(), save_evidence=AsyncMock(),
                               save_artifact=AsyncMock(side_effect=ValueError("metadata failed")))
        service = RunService(MagicMock(), None, MagicMock(), research_repository=repository,
                             artifact_storage=LocalArtifactStorage())
        run = RunDocument(run_id="metadata-failure", flow_id="fixture", user_id="owner")
        with self.assertRaises(ValueError):
            await service._persist_research_output(run, "report_agent", {
                "result_storage": [{"task_id": 1, "result": output}]})
        self.assertTrue(path.is_file())

    def test_release_cannot_delete_non_generated_or_durable_artifacts(self) -> None:
        storage = LocalArtifactStorage()
        outside = self.workspace / "private.md"
        outside.write_text("private", encoding="utf-8")
        with self.assertRaises(ValueError):
            storage.release_generated_file(str(outside))
        artifact = storage.ingest_file(str(outside), "owner", "run")
        with self.assertRaises(ValueError):
            storage.release_generated_file(str(storage.resolve(artifact.storage_uri)))
        self.assertTrue(outside.is_file())
        self.assertTrue(storage.resolve(artifact.storage_uri).is_file())

    async def test_report_worker_has_no_final_llm_rewrite_and_same_name_does_not_overwrite(self) -> None:
        model = MagicMock()
        worker = WorkerAgent("report_agent", "Render", model, [markdown_report_generator])
        worker.evidence_store = MagicMock()
        state = {"current_task": Task(id=3, node="report_agent", status="pending",
                                      description="Brief", dependencies=[2]),
                 "result_storage": [{"task_id": 2, "result": {
                     "findings": [SynthesisFinding(text=claim().claim, evidence_ids=["e1"]).model_dump()],
                     "evidence": [claim().model_dump()], "status": "partial"}}]}
        with artifact_scope("run-a"), patch.object(settings, "ENABLE_EXECUTION_BENCHMARK_METRICS", True):
            first = await worker.execute(state)
            second = await worker.execute(state)
        with artifact_scope("run-b"):
            third = await worker.execute(state)
        paths = [Path(output["result_storage"][0]["artifact_paths"][0]) for output in (first, second, third)]
        self.assertEqual(len(set(paths)), 3)
        self.assertTrue(all(path.is_file() for path in paths))
        self.assertNotEqual(paths[0].parent.parent, paths[2].parent.parent)
        self.assertEqual(first["current_task"].status, "partial")
        self.assertEqual(first["task_execution_metrics"][0]["node"], "report_agent")
        self.assertEqual(first["execution_timings"][0]["operation"], "tool")
        model.bind_tools.assert_not_called()
        model.with_structured_output.assert_not_called()

    async def test_source_outcomes_separate_fetch_empty_and_invalid_extraction(self) -> None:
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=ValueError("bad schema"))
        store = MagicMock(check_active=AsyncMock(), save_document=AsyncMock(), save_bundle=AsyncMock())
        processor = EvidenceProcessor(model, store, "run", "1")
        await processor.process(failure_result("internal_error", code="fetch_error", message="failed",
            source=SourceMetadata(requested_url="https://fixture.invalid/a")), "Research")
        await processor.process(success_result({"text": ""},
            source=SourceMetadata(final_url="https://fixture.invalid/b")), "Research")
        await processor.process(success_result({"text": claim().excerpt},
            source=SourceMetadata(final_url="https://fixture.invalid/c")), "Research")
        self.assertEqual({item.status for item in processor.bundle.source_outcomes},
                         {"fetch_failed", "empty", "invalid_extraction"})

    async def test_budget_counts_worker_mapper_and_reducer_together(self) -> None:
        evidence = claim()
        model = MagicMock()
        model.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))
        model.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=[
            ChunkExtraction(claims=[ExtractedClaim(**evidence.model_dump(include=set(ExtractedClaim.model_fields)))]),
            SynthesisResult(findings=[SynthesisFinding(text=evidence.claim, evidence_ids=["unused"])])])
        store = MagicMock(check_active=AsyncMock(), save_document=AsyncMock(), save_bundle=AsyncMock())
        budget = RunBudget(2, 100000, 10)
        with run_budget_scope(budget):
            await bounded_invoke(model, [HumanMessage(content="Worker")])
            processor = EvidenceProcessor(model, store, "run", "1")
            await processor.process(success_result({"text": evidence.excerpt},
                source=SourceMetadata(final_url=evidence.source_url)), "Context")
            with self.assertRaises(RunBudgetExceeded):
                await EvidenceReducer(model, store, "run", 2).reduce(processor.bundle.claims, "Context")
        self.assertEqual(budget.calls, 2)
        self.assertEqual(model.with_structured_output.return_value.ainvoke.await_count, 1)

    async def test_invalid_extraction_does_not_log_raw_model_payload(self) -> None:
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value={
            "claims": [{"claim": "private-model-payload", "excerpt": {"private": "source-data"}}]})
        store = MagicMock(check_active=AsyncMock(), save_document=AsyncMock(), save_bundle=AsyncMock())
        processor = EvidenceProcessor(model, store, "run", "1")
        with self.assertLogs("app.execution.research_evidence", "ERROR") as logs:
            await processor.process(success_result({"text": "source-data"},
                source=SourceMetadata(final_url="https://fixture.invalid/docs")), "Research")
        self.assertIsNone(logs.records[0].exc_info)
        self.assertEqual(logs.records[0].error_type, "ValidationError")
        self.assertNotIn("private-model-payload", " ".join(logs.output))

    async def test_token_budget_rejects_before_invocation_and_failed_calls_are_charged(self) -> None:
        model = MagicMock(ainvoke=AsyncMock(side_effect=ValueError("provider failed")))
        with run_budget_scope(RunBudget(10, 1, 10)), self.assertRaises(RunBudgetExceeded):
            await bounded_invoke(model, [HumanMessage(content="input")])
        model.ainvoke.assert_not_called()
        budget = RunBudget(1, 10000, 10)
        with run_budget_scope(budget):
            with self.assertRaises(ValueError):
                await bounded_invoke(model, [HumanMessage(content="input")])
            with self.assertRaises(RunBudgetExceeded):
                await bounded_invoke(model, [HumanMessage(content="input")])
        self.assertEqual(model.ainvoke.await_count, 1)
        self.assertGreater(budget.reserved_tokens, 0)

    async def test_deadline_cancels_inflight_operation(self) -> None:
        cancelled = asyncio.Event()
        async def slow() -> None:
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        with run_budget_scope(RunBudget(10, 10000, 0.02)), self.assertRaises(RunBudgetExceeded):
            await bounded_operation(slow)
        self.assertTrue(cancelled.is_set())

    async def test_cancellation_stops_operation_without_new_inference(self) -> None:
        active = True
        async def check() -> None:
            if not active:
                raise RunNoLongerActive("cancelled")
        async def slow() -> None:
            nonlocal active
            active = False
            await asyncio.sleep(10)
        with run_budget_scope(RunBudget(10, 10000, 10, check)), self.assertRaises(RunNoLongerActive):
            await bounded_operation(slow)

    async def test_active_check_interval_throttles_repeated_status_reads(self) -> None:
        clock = _FakeClock()
        checks = AsyncMock()
        budget = RunBudget(10, 10000, 10, checks, active_check_interval_seconds=1.0)
        with patch("app.execution.run_budget.monotonic", clock):
            await budget.ensure_active()
            await budget.ensure_active()
            clock.advance(0.9)
            await budget.ensure_active()
            self.assertEqual(checks.await_count, 1)
            clock.advance(0.2)
            await budget.ensure_active()
            await budget.ensure_active()
            self.assertEqual(checks.await_count, 2)

    async def test_active_check_interval_still_runs_the_first_check(self) -> None:
        clock = _FakeClock()
        checks = AsyncMock()
        budget = RunBudget(10, 10000, 10, checks,
                           active_check_interval_seconds=MAX_ACTIVE_CHECK_INTERVAL_SECONDS)
        with patch("app.execution.run_budget.monotonic", clock):
            await budget.ensure_active()
        self.assertEqual(checks.await_count, 1)

    async def test_zero_active_check_interval_checks_on_every_call(self) -> None:
        clock = _FakeClock()
        checks = AsyncMock()
        budget = RunBudget(10, 10000, 10, checks, active_check_interval_seconds=0.0)
        with patch("app.execution.run_budget.monotonic", clock):
            for _ in range(4):
                await budget.ensure_active()
        self.assertEqual(checks.await_count, 4)

    async def test_active_check_without_callback_is_a_noop(self) -> None:
        budget = RunBudget(1, 100, 10)
        self.assertIsNone(await budget.ensure_active())
        self.assertIsNone(budget.check_active)

    def test_negative_active_check_interval_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RunBudget(1, 100, 10, None, -1.0)

    def test_active_check_interval_boundaries_are_accepted(self) -> None:
        """The ceiling is inclusive: only values strictly above it are rejected."""
        for interval in (0.0, 1.0, MAX_ACTIVE_CHECK_INTERVAL_SECONDS):
            with self.subTest(interval=interval):
                budget = RunBudget(1, 100, 10, None, interval)
                self.assertEqual(budget.active_check_interval_seconds, interval)

    def test_non_finite_or_out_of_range_active_check_intervals_are_rejected(self) -> None:
        """``inf`` must be rejected: ``now - last < inf`` is always true.

        An infinite interval would allow exactly one active check per budget lifetime, so
        cancellation would never be observed again for that run; the constructor fails
        fast instead. ``nan`` is rejected for the same reason (every comparison with it is
        false, so the throttle silently stops working).
        """
        for interval in (2.5, -1.0, float("inf"), float("nan")):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    RunBudget(1, 100, 10, None, interval)

    async def test_cancellation_is_detected_past_the_active_check_interval(self) -> None:
        clock = _FakeClock()
        active = True
        async def check() -> None:
            if not active:
                raise RunNoLongerActive("cancelled")
        async def work() -> str:
            nonlocal active
            active = False
            clock.advance(2.0)
            return "finished"
        with patch("app.execution.run_budget.monotonic", clock):
            # The budget must be constructed INSIDE the patch. ``RunBudget.started`` samples
            # ``monotonic()`` in ``__init__``: built outside, it records real machine uptime
            # (e.g. 484132.7) while the fake clock starts at 1000.0. ``reserve()`` then sees
            # 1000.0 - 484132.7 >= 10 on a long-uptime host (passes), but 1000.0 - 5 >= 10 on
            # a freshly booted CI VM, raising RunBudgetExceeded instead of RunNoLongerActive.
            # RunBudgetExceeded is not a RunNoLongerActive subclass, so the test errors on CI
            # while passing locally.
            budget = RunBudget(10, 10000, 10, check, active_check_interval_seconds=1.0)
            self.assertEqual(budget.started, clock.start)  # fake clock, not real uptime
            with run_budget_scope(budget), self.assertRaises(RunNoLongerActive) as raised:
                await bounded_operation(work)
        self.assertEqual(raised.exception.code, "run_cancelled")
        self.assertNotIsInstance(raised.exception, RunBudgetExceeded)

    def test_budget_snapshot_excludes_tuning_knob_and_keeps_interval_attribute(self) -> None:
        """The persisted ``metadata["run_budget"]`` record is cumulative accounting only.

        It is read back as an audit record, so it must not grow tuning knobs: the active-check
        interval stays on the instance attribute and out of the snapshot.
        """
        budget = RunBudget(2, 100, 5, None, MAX_ACTIVE_CHECK_INTERVAL_SECONDS)
        snapshot = budget.snapshot()
        self.assertNotIn("active_check_interval_seconds", snapshot)
        self.assertEqual(budget.active_check_interval_seconds, MAX_ACTIVE_CHECK_INTERVAL_SECONDS)
        self.assertEqual(set(snapshot), {"calls", "reserved_estimated_tokens", "max_calls",
                                         "max_estimated_tokens", "max_seconds", "elapsed_seconds",
                                         "counting_method"})

    def test_config_ceiling_matches_the_library_ceiling(self) -> None:
        """Config and library bounds must not drift apart.

        No environment variable is read or mutated: the field value is injected directly so
        the assertion holds whatever the ambient ``.env`` says.
        """
        with self.assertRaises(ValidationError):
            Settings(_env_file=None,
                     RUN_ACTIVE_CHECK_INTERVAL_SECONDS=MAX_ACTIVE_CHECK_INTERVAL_SECONDS + 0.5)
        accepted = Settings(_env_file=None,
                            RUN_ACTIVE_CHECK_INTERVAL_SECONDS=MAX_ACTIVE_CHECK_INTERVAL_SECONDS)
        self.assertEqual(accepted.RUN_ACTIVE_CHECK_INTERVAL_SECONDS, MAX_ACTIVE_CHECK_INTERVAL_SECONDS)

    def test_configured_active_check_interval_is_accepted_by_the_budget(self) -> None:
        """The production default must pass library validation at construction time."""
        configured = settings.RUN_ACTIVE_CHECK_INTERVAL_SECONDS
        self.assertGreaterEqual(configured, 0.0)
        self.assertLessEqual(configured, MAX_ACTIVE_CHECK_INTERVAL_SECONDS)
        budget = RunBudget(1, 100, 10, None, configured)  # must not raise ValueError
        self.assertEqual(budget.active_check_interval_seconds, configured)

    async def test_run_deadline_persists_failure_and_duplicate_does_not_erase_budget(self) -> None:
        repository = PostgresRunRepository()
        run = RunDocument(run_id="ci-budget-fixture", flow_id="fixture", user_id="owner", status="queued",
                          plan=[Task(id=1, node="worker", status="pending", description="slow")])
        await repository.save(run)
        class SlowExecution:
            async def execute_run(self, run_id: str, state: dict):
                await asyncio.sleep(10)
                yield {}
        service = RunService(repository, None, SlowExecution())
        with patch.object(settings, "MAX_RUN_DURATION", 0.02):
            result = await service.execute_queued_run(run.run_id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "run_budget_exhausted")
        original = dict(result.metadata["run_budget"])
        duplicate = await service.execute_queued_run(run.run_id)
        self.assertEqual(duplicate.metadata["run_budget"], original)

    async def test_cancelled_run_stays_cancelled_during_inflight_node(self) -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()
        class WaitingExecution:
            async def execute_run(self, run_id: str, state: dict):
                try:
                    started.set()
                    await asyncio.sleep(10)
                    yield {}
                finally:
                    stopped.set()
        repository = PostgresRunRepository()
        run = RunDocument(run_id="cancel-budget-fixture", flow_id="fixture", user_id="owner", status="queued")
        await repository.save(run)
        service = RunService(repository, None, WaitingExecution())
        execution = asyncio.create_task(service.execute_queued_run(run.run_id))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await service.cancel_run(run.run_id, "owner")
            result = await asyncio.wait_for(execution, 2)
            self.assertEqual(result.status, "cancelled")
            self.assertTrue(stopped.is_set())
            self.assertEqual((await repository.get(run.run_id)).status, "cancelled")
        finally:
            if not execution.done():
                execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)

    async def test_queued_run_throttles_status_reads_across_poll_ticks(self) -> None:
        """P0 regression: a long node must not re-read run status on every 250 ms tick.

        The fake execution port waits 0.6 s of real time before ending, so ``bounded_stream``
        performs at least three 0.25 s poll iterations plus the unconditional pre-loop check:
        four ``ensure_active`` attempts. A naive implementation would issue four status reads.

        The throttle is measured with a FROZEN ``monotonic`` for the whole call, so the
        assertion is independent of how long the run takes on a stalled runner: every attempt
        after the first sees ``now - last == 0.0 < 1.0`` and is suppressed. Arithmetic of the
        exact bound: repository reads are 1 budget-setup snapshot read in
        ``execute_queued_run`` plus 1 active check = 2 total, so ``get_calls - 1 == 1``.
        """
        poll_seconds = 0.6
        poll_interval_seconds = 1.0
        frozen_clock = _FakeClock()

        class PollingExecution:
            async def execute_run(self, run_id: str, state: dict):
                await asyncio.sleep(poll_seconds)
                for chunk in ():
                    yield chunk

        class CountingRunRepository(PostgresRunRepository):
            def __init__(self) -> None:
                super().__init__()
                self.get_calls = 0

            async def get(self, run_id: str, user_id: str | None = None) -> RunDocument | None:
                self.get_calls += 1
                return await super().get(run_id, user_id)

        class CountingRunBudget(RunBudget):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.active_check_attempts = 0
                created.append(self)

            async def ensure_active(self) -> None:
                self.active_check_attempts += 1
                await super().ensure_active()

        created: list[CountingRunBudget] = []
        repository = CountingRunRepository()
        run = RunDocument(run_id="throttled-active-check-fixture", flow_id="fixture", user_id="owner",
                          status="queued",
                          plan=[Task(id=1, node="worker", status="done", description="slow node")])
        await repository.save(run)
        service = RunService(repository, None, PollingExecution())
        with patch.object(settings, "RUN_ACTIVE_CHECK_INTERVAL_SECONDS", poll_interval_seconds), \
                patch("app.modules.runs.service.RunBudget", CountingRunBudget), \
                patch("app.execution.run_budget.monotonic", frozen_clock):
            result = await service.execute_queued_run(run.run_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(created), 1)
        attempts = created[0].active_check_attempts
        active_reads = repository.get_calls - 1  # minus the budget-setup snapshot read
        self.assertEqual(created[0].started, frozen_clock.start)
        self.assertGreaterEqual(attempts, 4)
        self.assertEqual(active_reads, 1)
        self.assertLess(active_reads, attempts)
