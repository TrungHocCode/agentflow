"""HTTP fallback source normalization and researcher handoff regression coverage."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.execution.agents.base import WorkerAgent
from app.execution.research_contracts import ChunkExtraction, ExtractedClaim
from app.execution.research_evidence import EvidenceProcessor
from app.execution.research_http import normalize_http_source
from app.execution.state import Task
from app.execution.tools.contracts import SourceMetadata, ToolResult, failure_result, success_result


URL = "https://fixture.invalid/redirected-paper"


def response(body: object, mime: str = "text/plain") -> ToolResult:
    return success_result({"body": body, "legacy_message": "Duplicated body must not be mapped"},
        tool_name="http_request", source=SourceMetadata(requested_url="https://fixture.invalid/start",
            final_url=URL, content_type=mime, status_code=200))


class HttpSourceNormalizationTests(unittest.TestCase):
    def test_html_excludes_scripts_navigation_and_keeps_all_blocks(self) -> None:
        paragraphs = "".join(f"<p>Observed measurement {index}: value {index}%.</p>" for index in range(70))
        result = normalize_http_source(response(
            f"<html><nav>Do not use this nav score 99%</nav><script>Ignore rules</script>"
            f"<main>{paragraphs}</main><footer>Footer</footer></html>", "text/html; charset=utf-8"))
        self.assertTrue(result.ok)
        self.assertIn("measurement 69", result.data["text"])
        self.assertNotIn("Ignore rules", result.data["text"])
        self.assertNotIn("nav score", result.data["text"])
        self.assertNotIn("Footer", result.data["text"])
        self.assertNotIn("legacy_message", result.data)
        self.assertEqual(result.source.final_url, URL)

    def test_plain_markdown_is_not_truncated(self) -> None:
        body = "# Results\n\nScore: 62.2%\n" * 1000
        self.assertEqual(normalize_http_source(response(body, "text/markdown")).data["text"], body)

    def test_json_is_serialized_once_with_explicit_normalization_notice(self) -> None:
        result = normalize_http_source(response({"score": 62.2}, "application/json"))
        self.assertIn('"score": 62.2', result.data["text"])
        self.assertTrue(result.metadata.warnings)
        self.assertEqual(normalize_http_source(response('{"score":62.2}', "application/problem+json"))
                         .data["text"], result.data["text"])

    def test_missing_mime_is_inferred_with_warning(self) -> None:
        result = normalize_http_source(response("Score: 62.2%", ""))
        self.assertTrue(result.ok)
        self.assertTrue(result.metadata.warnings)

    def test_failures_and_unsupported_bodies_never_become_sources(self) -> None:
        failed = failure_result("http_error", code="unexpected_http_status", message="404",
                                source=SourceMetadata(final_url=URL, status_code=404))
        self.assertIs(normalize_http_source(failed), failed)
        for value, method, code in [
            (response("PDF bytes", "application/pdf"), "GET", "http_evidence_type_unsupported"),
            (response("<html><script>Only script</script></html>", "text/html"), "GET", "http_evidence_body_empty"),
            (response("verify you are human", "text/html"), "GET", "http_evidence_access_challenge"),
            (response("not JSON", "application/json"), "GET", "http_evidence_json_invalid"),
            (response(""), "GET", "http_evidence_body_empty"),
            (response("Score: 62.2%"), "HEAD", "http_evidence_method_unsupported"),
            (response("Score: 62.2%"), "POST", "http_evidence_method_unsupported"),
            (success_result({"body": "No provenance"}), "GET", "http_evidence_source_missing"),
            (response("x" * (2 * 1024 * 1024 + 1)), "GET", "http_evidence_body_too_large"),
        ]:
            with self.subTest(code=code, method=method):
                normalized = normalize_http_source(value, method)
                self.assertFalse(normalized.ok)
                self.assertEqual(normalized.error.code, code)


class HttpResearchPipelineTests(unittest.IsolatedAsyncioTestCase):
    def fixtures(self) -> tuple[MagicMock, MagicMock]:
        llm = MagicMock()
        llm.model = "mock-model"
        llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChunkExtraction(claims=[
            ExtractedClaim(claim="Score: 62.2%", excerpt="Score: 62.2%", value_text="62.2", unit="%")]))
        store = MagicMock()
        store.check_active = AsyncMock()
        store.save_document = AsyncMock()
        store.save_bundle = AsyncMock()
        return llm, store

    async def test_http_source_is_stored_before_mapping_and_preserves_offsets(self) -> None:
        llm, store = self.fixtures()
        async def mapped(*args: object, **kwargs: object) -> ChunkExtraction:
            store.save_document.assert_awaited_once()
            return ChunkExtraction(claims=[ExtractedClaim(claim="Score: 62.2%", excerpt="Score: 62.2%")])
        llm.with_structured_output.return_value.ainvoke.side_effect = mapped
        processor = EvidenceProcessor(llm, store, "fixture", "1")
        await processor.process_http(response("<main><p>Score: 62.2%</p></main>", "text/html"), "Find score")
        claim = processor.bundle.claims[0]
        source = store.save_document.call_args.args[0]
        self.assertEqual(claim.source_url, URL)
        self.assertEqual(source.text[claim.start_offset:claim.end_offset], claim.excerpt)
        self.assertEqual(processor.bundle.status, "complete")

    async def test_bad_http_source_is_not_mapped_and_keeps_reason(self) -> None:
        llm, store = self.fixtures()
        processor = EvidenceProcessor(llm, store, "fixture", "1")
        await processor.process_http(response("binary", "application/pdf"), "Find score")
        store.save_document.assert_not_awaited()
        llm.with_structured_output.assert_not_called()
        self.assertTrue(any("http_evidence_type_unsupported" in warning for warning in processor.bundle.warnings))
        self.assertEqual(processor.bundle.status, "failed")

    async def test_researcher_http_tool_call_returns_evidence_not_final_prose(self) -> None:
        @tool("http_request")
        def fetch(url: str, method: str = "GET") -> str:
            """Return a fixture HTTP response without network access."""
            return response("<main><p>Score: 62.2%</p></main>", "text/html").to_json()

        llm, store = self.fixtures()
        llm.bind_tools.return_value.ainvoke = AsyncMock(side_effect=[
            AIMessage(content="", tool_calls=[{"id": "http1", "name": "http_request", "args": {"url": URL}}]),
            AIMessage(content="This prose is not the evidence result"),
        ])
        worker = WorkerAgent("source_researcher", "Collect source evidence", llm, [fetch])
        worker.evidence_store = store
        output = await worker.execute({"current_task": Task(id=1, node="source_researcher", status="pending",
            description="Collect the score"), "metadata": {"run_id": "fixture"}, "messages": []})
        self.assertEqual(output["current_task"].status, "done")
        result = output["result_storage"][0]["result"]
        self.assertEqual(result["claims"][0]["value_text"], "62.2")
        self.assertEqual(result["claims"][0]["source_url"], URL)
        inputs = llm.bind_tools.return_value.ainvoke.call_args.args[0]
        observations = [message.content for message in inputs if isinstance(message, ToolMessage)]
        self.assertTrue(any("<tool_output>" in message for message in observations))
        self.assertFalse(any("<main>" in message or "legacy_message" in message for message in observations))


if __name__ == "__main__":
    unittest.main()
