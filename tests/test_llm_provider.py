import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import settings  # noqa: E402
from app.execution.llm import get_llm  # noqa: E402
from app.infrastructure.model_probe import OllamaModelProbe  # noqa: E402


class TestLlmProvider(unittest.TestCase):
    def test_ollama_is_default(self) -> None:
        from langchain_ollama import ChatOllama

        self.assertEqual(settings.LLM_PROVIDER, "ollama")
        self.assertIsInstance(get_llm(), ChatOllama)

    def test_llamacpp_uses_openai_client_with_settings(self) -> None:
        from langchain_openai import ChatOpenAI

        with patch.object(settings, "LLM_PROVIDER", "llamacpp"), \
                patch.object(settings, "LLAMACPP_BASE_URL", "http://llama.test:8080/v1"), \
                patch.object(settings, "LLM_THINKING", None):
            llm = get_llm(model_name_override="m.gguf", temperature=0.2)
            self.assertIsInstance(llm, ChatOpenAI)
            self.assertEqual(llm.model_name, "m.gguf")
            self.assertEqual(llm.openai_api_base, "http://llama.test:8080/v1")
            self.assertIsNone(llm.extra_body)
            self.assertEqual(get_llm(base_url="http://other/v1").openai_api_base, "http://other/v1")

    def test_thinking_false_maps_to_chat_template_kwargs(self) -> None:
        with patch.object(settings, "LLM_PROVIDER", "llamacpp"), patch.object(settings, "LLM_THINKING", False):
            self.assertEqual(get_llm().extra_body, {"chat_template_kwargs": {"enable_thinking": False}})


class TestProbePerProvider(unittest.TestCase):
    @staticmethod
    def run_probe(provider: str, handler, base_url: str):
        with patch.object(settings, "LLM_PROVIDER", provider):
            probe = OllamaModelProbe(base_url=base_url, model="qwen3:8b", transport=httpx.MockTransport(handler))
            return asyncio.run(probe.check())

    def test_llamacpp_probe_lists_v1_models(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"data": [{"id": "whatever.gguf"}]})

        self.assertEqual(self.run_probe("llamacpp", handler, "http://llama.test/v1").status, "ok")
        self.assertEqual(seen, ["/v1/models"])

    def test_llamacpp_probe_rejects_bad_payload(self) -> None:
        report = self.run_probe("llamacpp", lambda _: httpx.Response(200, json=[]), "http://llama.test/v1")
        self.assertEqual(report.status, "unavailable")

    def test_ollama_probe_still_uses_api_tags(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

        self.assertEqual(self.run_probe("ollama", handler, "http://ollama.test").status, "ok")
        self.assertEqual(seen, ["/api/tags"])


if __name__ == "__main__":
    unittest.main()
