import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

import httpx
from langchain_core.language_models import BaseChatModel

from app.execution.agents.base import WorkerAgent
from app.execution.llm import (
    MODEL_NOT_FOUND, MODEL_OUT_OF_MEMORY, MODEL_UNAVAILABLE, MODEL_UNREACHABLE, classify_model_failure,
)
from app.execution.state import Task
from app.infrastructure.model_probe import OllamaModelProbe


class ResponseError(Exception):
    """Stands in for ollama.ResponseError, which is matched by name."""

    def __init__(self, error: str, status_code: int = 500) -> None:
        super().__init__(error)
        self.status_code = status_code


class TestClassifyModelFailure(unittest.TestCase):
    def test_out_of_memory_on_gpu_and_cpu(self) -> None:
        for text in ("llama-server reported out-of-memory during startup: cudaMalloc failed: out of memory",
                     "failed to allocate CPU_REPACK buffer of size 1765048320",
                     "unable to allocate CUDA0 buffer"):
            with self.subTest(text=text):
                self.assertIs(classify_model_failure(ResponseError(text)), MODEL_OUT_OF_MEMORY)

    def test_missing_model(self) -> None:
        self.assertIs(classify_model_failure(ResponseError('model "x" not found', 404)), MODEL_NOT_FOUND)

    def test_other_server_error(self) -> None:
        self.assertIs(classify_model_failure(ResponseError("boom", 500)), MODEL_UNAVAILABLE)

    def test_unreachable_server(self) -> None:
        self.assertIs(classify_model_failure(httpx.ConnectError("refused")), MODEL_UNREACHABLE)
        self.assertIs(classify_model_failure(ConnectionError("refused")), MODEL_UNREACHABLE)

    def test_wrapped_failure_is_found_through_the_cause_chain(self) -> None:
        try:
            try:
                raise ResponseError("cudaMalloc failed: out of memory")
            except ResponseError as inner:
                raise RuntimeError("structured output failed") from inner
        except RuntimeError as outer:
            self.assertIs(classify_model_failure(outer), MODEL_OUT_OF_MEMORY)

    def test_unrelated_errors_are_not_blamed_on_the_model(self) -> None:
        self.assertIsNone(classify_model_failure(ValueError("bad schema")))
        self.assertIsNone(classify_model_failure(RuntimeError("not found in plan")))

    def test_messages_do_not_expose_the_raw_server_text(self) -> None:
        failure = classify_model_failure(ResponseError("secret-internal-path out of memory"))
        self.assertNotIn("secret-internal-path", failure.message)


class TestWorkerReportsModelFailure(unittest.IsolatedAsyncioTestCase):
    async def test_failed_task_carries_a_clear_reason(self) -> None:
        llm = MagicMock(spec=BaseChatModel)
        llm.ainvoke = AsyncMock(side_effect=ResponseError("cudaMalloc failed: out of memory"))
        agent = WorkerAgent(name="worker", system_prompt="Do the task.", llm=llm)
        task = Task(id=1, node="worker", status="running", description="Collect")

        output = await agent.execute({"current_task": task, "messages": [], "metadata": {}})

        self.assertEqual(output["plan"][0].status, "failed")
        self.assertEqual(output["plan"][0].error, MODEL_OUT_OF_MEMORY.message)
        last_error = output["metadata"]["last_error"]
        self.assertEqual(last_error["code"], MODEL_OUT_OF_MEMORY.code)
        self.assertEqual(last_error["message"], MODEL_OUT_OF_MEMORY.message)
        self.assertTrue(last_error["retryable"])
        self.assertEqual(last_error["category"], "dependency")


class TestOllamaModelProbe(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def probe(handler, model: str = "qwen3:8b") -> OllamaModelProbe:
        return OllamaModelProbe(base_url="http://ollama.test", model=model, transport=httpx.MockTransport(handler))

    async def test_installed_model_is_ok(self) -> None:
        report = await self.probe(lambda _: httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})).check()
        self.assertEqual(report.status, "ok")
        self.assertIsNone(report.code)

    async def test_untagged_model_matches_latest(self) -> None:
        handler = lambda _: httpx.Response(200, json={"models": [{"name": "gemma2:latest"}]})
        self.assertEqual((await self.probe(handler, model="gemma2").check()).status, "ok")

    async def test_missing_model(self) -> None:
        report = await self.probe(lambda _: httpx.Response(200, json={"models": [{"name": "other:1b"}]})).check()
        self.assertEqual((report.status, report.code), ("unavailable", MODEL_NOT_FOUND.code))

    async def test_unreachable_server_does_not_raise(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        report = await self.probe(refuse).check()
        self.assertEqual((report.status, report.code), ("unavailable", MODEL_UNREACHABLE.code))

    async def test_bad_server_response_does_not_raise(self) -> None:
        for response in (httpx.Response(500), httpx.Response(200, text="not json"), httpx.Response(200, json=[])):
            with self.subTest(response=response):
                report = await self.probe(lambda _: response).check()
                self.assertEqual((report.status, report.code), ("unavailable", MODEL_UNAVAILABLE.code))


if __name__ == "__main__":
    unittest.main()
