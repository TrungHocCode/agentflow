"""CI-P1 core contracts; mocked inference, isolated files, no development DB."""

import asyncio
import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.core.config import settings
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
    RunBudget, RunBudgetExceeded, RunNoLongerActive, bounded_invoke, bounded_operation, run_budget_scope,
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
