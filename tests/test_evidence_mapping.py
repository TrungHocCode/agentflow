"""Deterministic extraction, provenance and cancellation tests."""

import asyncio
import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.config import settings
from app.execution.research_contracts import ChunkExtraction, ExtractedClaim
from app.execution.research_evidence import EvidenceProcessor, chunks
from app.execution.tools.contracts import SourceMetadata, success_result
from app.infrastructure.artifacts.evidence_store import DurableEvidenceStore


class EvidenceMappingTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, output: ChunkExtraction) -> tuple[EvidenceProcessor, MagicMock, MagicMock]:
        model = MagicMock()
        model.model = "mock-model"
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=output)
        store = MagicMock()
        store.save_document = AsyncMock()
        store.save_bundle = AsyncMock()
        store.check_active = AsyncMock()
        return EvidenceProcessor(model, store, "run", "1"), model, store

    def test_chunks_cover_every_character(self) -> None:
        text = "Header\n\n" + "abc\n\n" * 1000
        parts = chunks(text, 200)
        self.assertEqual("".join(piece for _, _, piece in parts), text)
        self.assertTrue(all(len(piece) <= 200 for _, _, piece in parts))
        self.assertEqual(parts[-1][1], len(text))

    async def test_verified_number_preserved_and_source_identity_is_backend_owned(self) -> None:
        extraction = ChunkExtraction(claims=[ExtractedClaim(claim="Model scored 62.2%", excerpt="Score: 62.2%",
                                                           value_text="62.2", unit="%")])
        processor, model, store = self.fixture(extraction)
        await processor.process(success_result({"text": "Table\nScore: 62.2%\nEnd"},
            source=SourceMetadata(final_url="https://example.org/paper")), "Compare scores")
        claim = processor.bundle.claims[0]
        self.assertEqual(claim.value_text, "62.2")
        self.assertEqual(claim.source_url, "https://example.org/paper")
        document = store.save_document.call_args.args[0]
        self.assertEqual(document.text[claim.start_offset:claim.end_offset], claim.excerpt)
        store.save_document.assert_awaited_once()
        model.bind_tools.assert_not_called()

    async def test_hallucinated_excerpt_and_number_are_rejected(self) -> None:
        processor, _, _ = self.fixture(ChunkExtraction(claims=[
            ExtractedClaim(claim="Invented", excerpt="Does not exist"),
            ExtractedClaim(claim="Wrong number", excerpt="Score: 62.2%", value_text="99.9")]))
        await processor.process(success_result({"text": "Score: 62.2%"},
            source=SourceMetadata(final_url="https://example.org/paper")), "Compare")
        self.assertEqual(processor.bundle.claims, [])
        self.assertEqual(processor.bundle.status, "failed")

    async def test_chunk_budget_keeps_saved_raw_source_and_marks_unprocessed(self) -> None:
        processor, model, store = self.fixture(ChunkExtraction())
        with patch.object(settings, "RESEARCH_MAX_CHUNKS", 1), patch.object(settings, "RESEARCH_CHUNK_CHARS", 128):
            await processor.process(success_result({"text": "x" * 500},
                source=SourceMetadata(final_url="https://example.org/paper")), "Compare")
        self.assertEqual(model.with_structured_output.return_value.ainvoke.await_count, 1)
        self.assertEqual(processor.bundle.unprocessed_chunks, 3)
        self.assertEqual(len(store.save_document.call_args.args[0].text), 500)

    async def test_cancellation_does_not_become_an_extraction_warning(self) -> None:
        processor, _, store = self.fixture(ChunkExtraction())
        store.check_active.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await processor.process(success_result({"text": "Source content"},
                source=SourceMetadata(final_url="https://example.org/paper")), "Compare")

    async def test_one_failed_chunk_does_not_discard_other_evidence(self) -> None:
        processor, model, _ = self.fixture(ChunkExtraction(claims=[
            ExtractedClaim(claim="Observed", excerpt="Score: 62.2%")]))
        model.with_structured_output.return_value.ainvoke.side_effect = [
            ValueError("Malformed output"), ChunkExtraction(claims=[
                ExtractedClaim(claim="Observed", excerpt="Score: 62.2%")])]
        with patch.object(settings, "RESEARCH_CHUNK_CHARS", 128):
            await processor.process(success_result({"text": "x" * 128 + "Score: 62.2%"},
                source=SourceMetadata(final_url="https://example.org/paper")), "Compare")
        self.assertEqual(len(processor.bundle.claims), 1)
        self.assertEqual(processor.bundle.status, "partial")

    async def test_raw_source_storage_is_run_scoped_and_metadata_excludes_body(self) -> None:
        from app.execution.research_contracts import SourceDocument
        repository = MagicMock()
        repository.save_result = AsyncMock()
        runs = MagicMock()
        runs.get = AsyncMock(return_value=MagicMock(user_id="owner", status="running"))
        with tempfile.TemporaryDirectory() as root:
            store = DurableEvidenceStore(repository, root, runs)
            document = SourceDocument(document_id="doc", run_id="cb2957ac-f87d-4e59-a513-2af4cbb8cd32",
                                      task_id="1", source_url="https://example.org/paper", content_hash="hash",
                                      text="Full original content")
            await store.save_document(document)
            record = repository.save_result.call_args.args[0]
            self.assertNotIn("text", record.content)
            source = Path(root) / record.content["storage_uri"]
            self.assertEqual(source.read_text(encoding="utf-8"), document.text)
            self.assertIn(document.run_id, str(source))
            with self.assertRaises(ValueError):
                await store.save_document(document.model_copy(update={"text": "Overwrite"}))
