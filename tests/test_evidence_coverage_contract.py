"""Chunk-local gaps and exact literals cannot become unsupported global findings."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from pydantic import ValidationError

from app.core.config import settings
from app.execution.agents.base import WorkerAgent
from app.execution.research_contracts import (
    ChunkExtraction, EvidenceClaim, ExtractedClaim, ResearchResult, SynthesisFinding, SynthesisResult,
)
from app.execution.research_evidence import EvidenceProcessor
from app.execution.research_reduction import EvidenceReducer
from app.execution.research_validation import supported_literals
from app.execution.state import SupervisorOutput, Task
from app.execution.tools.contracts import SourceMetadata, success_result


class EvidenceCoverageContractTests(unittest.IsolatedAsyncioTestCase):
    def model_and_store(self) -> tuple[MagicMock, MagicMock]:
        model = MagicMock()
        model.model = "fixture"
        store = MagicMock()
        for method in ("check_active", "save_document", "save_bundle"):
            setattr(store, method, AsyncMock())
        return model, store

    async def test_gap_in_one_chunk_is_not_global_missing_fact(self) -> None:
        model, store = self.model_and_store()
        model.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=[
            ChunkExtraction(missing_fields=["price", "price"]),
            ChunkExtraction(claims=[ExtractedClaim(
                claim="Price: 20 USD", excerpt="Price: 20 USD", metric="price", value_text="20", unit="USD",
            )]),
        ])
        processor = EvidenceProcessor(model, store, "run", "1")
        with patch.object(settings, "RESEARCH_CHUNK_CHARS", 128):
            output = await processor.process(success_result(
                {"text": "x" * 128 + "Price: 20 USD"},
                source=SourceMetadata(final_url="https://fixture.invalid/pricing"),
            ), "Find price")
        self.assertEqual(len(processor.bundle.claims), 1)
        self.assertEqual(processor.bundle.missing_fields, [])
        self.assertEqual(processor.bundle.chunk_diagnostics[0].missing_fields, ["price"])
        self.assertEqual(output.data["coverage_scope"], "extraction_only")
        self.assertNotIn("coverage", output.data)

    async def test_chunk_gap_is_retained_without_claiming_source_absence(self) -> None:
        model, store = self.model_and_store()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChunkExtraction(
            missing_fields=["release date"],
        ))
        processor = EvidenceProcessor(model, store, "run", "1")
        await processor.process(success_result(
            {"text": "A partial page excerpt"}, source=SourceMetadata(final_url="https://fixture.invalid/docs"),
        ), "Find release date")
        restored = ResearchResult.model_validate(store.save_bundle.call_args.args[2].model_dump())
        self.assertEqual(restored.chunk_diagnostics[0].missing_fields, ["release date"])
        self.assertEqual(restored.missing_fields, [])
        self.assertEqual(restored.status, "failed")

    async def test_synthesis_retains_scoped_diagnostics_and_labels_legacy_gaps(self) -> None:
        model, store = self.model_and_store()
        claim = EvidenceClaim(evidence_id="e1", document_id="d1", chunk_id="c1",
            source_url="https://fixture.invalid/docs", start_offset=0, end_offset=12,
            claim="Price 20 USD", excerpt="Price 20 USD", value_text="20")
        bundle = ResearchResult(claims=[claim], status="partial", missing_fields=["legacy field"],
            chunk_diagnostics=[{"document_id": "d1", "chunk_id": "c2", "missing_fields": ["price"]}])
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=SynthesisResult(
            findings=[SynthesisFinding(text="Price 20 USD", evidence_ids=["e1"])],
        ))
        worker = WorkerAgent("synthesis_agent", "Compare", model)
        worker.evidence_store = store
        output = await worker.execute({"current_task": Task(id=2, node="synthesis_agent", status="pending",
            description="Compare", dependencies=[1]), "metadata": {"run_id": "run"},
            "result_storage": [{"task_id": 1, "result": bundle.model_dump()}]})
        result = output["result_storage"][0]["result"]
        self.assertEqual(result["chunk_diagnostics"][0]["chunk_id"], "c2")
        self.assertIn("not publisher absence", result["limitations"][0])
        self.assertEqual(result["coverage_scope"], "extraction_only")

    async def test_reducer_rejects_changed_tag_and_keeps_exact_tag(self) -> None:
        for tag, valid in (("<tool_call>", False), ("<think>", True)):
            with self.subTest(tag=tag):
                model, store = self.model_and_store()
                model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=SynthesisResult(
                    findings=[SynthesisFinding(text=f"Uses {tag} tags", evidence_ids=["e1"])],
                ))
                claim = EvidenceClaim(evidence_id="e1", document_id="d1", chunk_id="c1",
                    source_url="https://fixture.invalid/docs", start_offset=0, end_offset=21,
                    claim="Uses <think> tags", excerpt="Uses <think> tags")
                reducer = EvidenceReducer(model, store, "run", 1)
                if valid:
                    self.assertEqual((await reducer.reduce([claim], "Tags")).findings[0].text, "Uses <think> tags")
                else:
                    with self.assertRaisesRegex(ValueError, "literal"):
                        await reducer.reduce([claim], "Tags")

    def test_code_identifier_spelling_is_case_sensitive(self) -> None:
        self.assertTrue(supported_literals("Use `max_tokens`", "Set max_tokens"))
        self.assertFalse(supported_literals("Use `maxTokens`", "Set max_tokens"))

    def test_empty_plan_is_invalid_but_clarification_is_valid(self) -> None:
        with self.assertRaises(ValidationError):
            SupervisorOutput(decision="propose_plan", assistant_message="Approve this", plan=[])
        for decision in ("clarify", "answer"):
            self.assertEqual(SupervisorOutput(decision=decision, assistant_message="A response").plan, [])

    def test_legacy_bundle_remains_readable(self) -> None:
        bundle = ResearchResult.model_validate({"schema_version": "1", "claims": [], "missing_fields": ["price"]})
        self.assertEqual(bundle.missing_fields, ["price"])
        self.assertEqual(bundle.chunk_diagnostics, [])
