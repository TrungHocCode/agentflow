"""Real-source-shaped regressions for source-owned quotes and numeric entity names."""

import json
import logging
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.execution.agents.base import _research_question
from app.execution.research_contracts import ChunkExtraction, EvidenceClaim, ExtractedClaim, SynthesisFinding, SynthesisResult
from app.execution.research_evidence import EvidenceProcessor
from app.execution.research_reduction import EvidenceReducer
from app.execution.research_validation import source_spans, validate_candidate
from app.execution.state import Task
from app.execution.tools.contracts import SourceMetadata, success_result
from app.shared.observability import JsonLogFormatter


SUBJECT = "Qwen3-4B-Instruct-2507"
DOCUMENT = (f"{SUBJECT}\n\nNumber of Parameters: 4.0B\n\nContext Length: 262,144 natively .\n\n"
            "NOTE: This model supports only non-thinking mode and does not generate <think></think> blocks.")


class SourceCandidateValidationTests(unittest.TestCase):
    def validate(self, candidate: ExtractedClaim) -> tuple:
        return validate_candidate(candidate, DOCUMENT, DOCUMENT, source_spans(DOCUMENT))

    def test_model_version_digits_are_context_not_measurements(self) -> None:
        claim, offset, reason = self.validate(ExtractedClaim(claim=f"{SUBJECT} has 4.0B parameters.",
            subject=SUBJECT, excerpt="Number of Parameters: 4.0B", value_text="4.0", unit="B"))
        self.assertIsNone(reason)
        self.assertEqual(claim.claim, "Number of Parameters: 4.0B")
        self.assertEqual(DOCUMENT[offset:offset+len(claim.excerpt)], claim.excerpt)

    def test_source_spelling_is_restored_without_rounding(self) -> None:
        claim, _, reason = self.validate(ExtractedClaim(claim=f"{SUBJECT} supports 262144 natively.",
            subject=SUBJECT, excerpt="Context Length: 262,144 natively .", value_text="262144"))
        self.assertIsNone(reason)
        self.assertEqual(claim.value_text, "262,144")
        self.assertIn("262,144", claim.claim)

    def test_span_id_resolves_literal_think_tags_without_model_copying(self) -> None:
        spans = source_spans(DOCUMENT)
        identity = next(key for key,value in spans.items() if "<think>" in value[1])
        claim, _, reason = self.validate(ExtractedClaim(claim="Supports only non-thinking mode.",
            subject=SUBJECT, excerpt="A corrupted paraphrase", source_span_id=identity))
        self.assertIsNone(reason)
        self.assertIn("<think></think>", claim.excerpt)
        self.assertEqual(claim.claim, claim.excerpt)

    def test_unknown_span_cannot_fall_back_to_an_unverified_excerpt(self) -> None:
        _, _, reason = self.validate(ExtractedClaim(claim="Observed", excerpt="Number of Parameters: 4.0B",
                                                   source_span_id="s999"))
        self.assertEqual(reason, "unknown_source_span")

    def test_unknown_subject_and_invented_measurement_are_rejected(self) -> None:
        for candidate, expected in [
            (ExtractedClaim(claim="Qwen3-8B has 4.0B parameters", subject="Qwen3-8B",
                            excerpt="Number of Parameters: 4.0B"), "subject_not_found"),
            (ExtractedClaim(claim=f"{SUBJECT} has 8.0B parameters", subject=SUBJECT,
                            excerpt="Number of Parameters: 4.0B"), "unsupported_measurement"),
            (ExtractedClaim(claim="Observed", excerpt="Number of Parameters: 4.0B", value_text="99.9"),
             "value_not_found"),
            (ExtractedClaim(claim="Observed", excerpt="Context Length: 262,144 natively .", unit="%"),
             "unit_not_found"),
            (ExtractedClaim(claim="Observed", excerpt="Not in the source"), "excerpt_not_found"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(self.validate(candidate)[2], expected)

    def test_rounded_scores_and_ambiguous_comma_decimals_are_not_equated(self) -> None:
        for value in ("62.3", "62,2", "99.9"):
            candidate = ExtractedClaim(claim=f"Scored {value}%", excerpt="Score: 62.2%", value_text=value)
            self.assertEqual(validate_candidate(candidate, "Score: 62.2%", "Score: 62.2%", {})[2],
                             "unsupported_measurement")

    def test_span_offsets_are_exact_for_long_multiline_paragraphs(self) -> None:
        text = "  First paragraph\n\n" + "x"*1800 + "\n\nLast paragraph"
        spans = source_spans(text)
        self.assertGreater(len(spans), 3)
        for offset, excerpt in spans.values():
            self.assertLessEqual(len(excerpt), 800)
            self.assertEqual(text[offset:offset+len(excerpt)], excerpt)

    def test_question_uses_user_research_request_not_run_wrapper(self) -> None:
        # A full backend span must remain valid when converted to the durable claim schema.
        text = "x" * 800
        candidate = ExtractedClaim(claim="Observed", excerpt=text, source_span_id="s0")
        validated, _, reason = validate_candidate(candidate, text, text, source_spans(text))
        self.assertIsNone(reason)
        self.assertEqual(len(EvidenceClaim(
            **validated.model_dump(), evidence_id="long", document_id="doc", chunk_id="chunk",
            source_url="https://fixture.invalid/card", start_offset=0, end_offset=800,
        ).claim), 800)

        task = Task(id=1,node="source_researcher",status="pending",description="Perform HTTP GET")
        question = _research_question({"metadata":{"input_data":{"user_prompt":"Find the stated context length"}}},task)
        self.assertEqual(question,"Find the stated context length")
        self.assertNotIn("Perform HTTP",question)
        self.assertEqual(_research_question({},task),task.description)

    def test_rejection_logging_has_reason_but_no_source_or_candidate_text(self) -> None:
        record = logging.LogRecord("research",logging.WARNING,__file__,1,"Evidence candidate rejected",(),None)
        record.rejection_reason = "unsupported_measurement"
        record.chunk_id = "hash:0"
        record.source_span_id = "s2"
        record.prompt_version = "evidence-map-v2"
        record.excerpt = "Private source text"
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["rejection_reason"],"unsupported_measurement")
        self.assertEqual(payload["source_span_id"],"s2")
        self.assertNotIn("Private source text",json.dumps(payload))


class SourcePipelineValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_spans_and_rejections_are_persisted(self) -> None:
        model = MagicMock()
        model.model = "mock-model"
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChunkExtraction(claims=[
            ExtractedClaim(claim=f"{SUBJECT} has 4.0B parameters",subject=SUBJECT,
                           excerpt="Wrongly copied text",source_span_id="s1",value_text="4.0",unit="B"),
            ExtractedClaim(claim="A made-up score of 99.9%",excerpt="Wrong",source_span_id="s1")]))
        store = MagicMock()
        store.check_active = AsyncMock()
        store.save_document = AsyncMock()
        store.save_bundle = AsyncMock()
        processor = EvidenceProcessor(model,store,"run","1")
        await processor.process(success_result({"text":DOCUMENT},source=SourceMetadata(final_url="https://example.org/card")),
                                "Find parameter count")
        self.assertEqual(len(processor.bundle.claims),1)
        self.assertEqual(processor.bundle.rejection_counts,{"unsupported_measurement":1})
        human = model.with_structured_output.return_value.ainvoke.call_args.args[0][1]
        self.assertEqual(json.loads(human.content)["source_spans"][1]["id"],"s1")
        self.assertEqual(processor.bundle.claims[0].excerpt,"Number of Parameters: 4.0B")
        store.save_bundle.assert_awaited()

    async def test_two_measurements_in_one_span_keep_distinct_evidence_ids(self) -> None:
        paragraph="Metric A: 62.2%; Metric B: 70.1%."
        model=MagicMock()
        model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=ChunkExtraction(claims=[
            ExtractedClaim(claim=paragraph,excerpt=paragraph,source_span_id="s0",metric="A",value_text="62.2"),
            ExtractedClaim(claim=paragraph,excerpt=paragraph,source_span_id="s0",metric="B",value_text="70.1")]))
        store=MagicMock()
        store.check_active=AsyncMock()
        store.save_document=AsyncMock()
        store.save_bundle=AsyncMock()
        processor=EvidenceProcessor(model,store,"run","1")
        await processor.process(success_result({"text":paragraph},source=SourceMetadata(final_url="https://example.org/card")),
                                "Extract both metrics")
        self.assertEqual(len({claim.evidence_id for claim in processor.bundle.claims}),2)

    async def test_reducer_allows_verified_entity_digits_but_not_invented_scores(self) -> None:
        model=MagicMock()
        model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=SynthesisResult(findings=[
            SynthesisFinding(text=f"{SUBJECT} has 4.0B parameters.",evidence_ids=["e1"])]))
        store=MagicMock()
        store.check_active=AsyncMock()
        evidence=EvidenceClaim(evidence_id="e1",document_id="doc",chunk_id="c0",source_url="https://example.org/card",
            start_offset=0,end_offset=28,claim="Number of Parameters: 4.0B",excerpt="Number of Parameters: 4.0B",
            subject=SUBJECT,value_text="4.0",unit="B")
        result=await EvidenceReducer(model,store,"run",2).reduce([evidence],"Find parameters")
        self.assertEqual(len(result.findings),1)
        model.with_structured_output.return_value.ainvoke.return_value=SynthesisResult(findings=[
            SynthesisFinding(text=f"{SUBJECT} scored 99.9%",evidence_ids=["e1"])])
        with self.assertRaises(ValueError):
            await EvidenceReducer(model,store,"run",2).reduce([evidence],"Find score")
