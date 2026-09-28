import json
import unittest

import httpx

from evaluation.ollama_benchmark import (
    measure_ollama_request,
    summarize_ollama_benchmark,
)


class TestOllamaBenchmark(unittest.IsolatedAsyncioTestCase):
    async def test_measure_raw_stream_and_provider_statistics_without_content(self):
        lines = [
            {"response": "first", "done": False},
            {"response": "second", "done": False},
            {
                "response": "",
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 5,
                "total_duration": 200_000_000,
                "load_duration": 10_000_000,
                "prompt_eval_duration": 40_000_000,
                "eval_duration": 100_000_000,
            },
        ]

        def handler(request):
            self.assertEqual(request.url.path, "/api/generate")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "qwen3:8b")
            self.assertTrue(body["stream"])
            return httpx.Response(
                200,
                content="\n".join(json.dumps(line) for line in lines).encode(),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sample = await measure_ollama_request(
                client,
                endpoint="http://ollama.test/api/generate",
                model="qwen3:8b",
                max_output_tokens=64,
                prompt="private prompt that must not be retained",
            )

        self.assertEqual(sample.status, "success")
        self.assertEqual(sample.input_tokens, 12)
        self.assertEqual(sample.output_tokens, 5)
        self.assertEqual(sample.model_load_ms, 10)
        self.assertEqual(sample.prompt_eval_ms, 40)
        self.assertEqual(sample.generation_ms, 100)
        self.assertEqual(sample.output_tokens_per_second, 50)
        self.assertEqual(sample.stream_chunk_count, 2)
        serialized = sample.model_dump_json()
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("first", serialized)

    async def test_failed_response_records_failure_without_leaking_body(self):
        def handler(request):
            return httpx.Response(503, text="provider body contains private content")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sample = await measure_ollama_request(
                client,
                endpoint="http://ollama.test/api/generate",
                model="qwen3:8b",
                max_output_tokens=64,
                prompt="secret",
            )

        self.assertEqual(sample.status, "failed")
        self.assertEqual(sample.error_type, "HTTPStatusError")
        self.assertNotIn("private content", sample.model_dump_json())

    async def test_timeout_is_classified_without_storing_sensitive_url(self):
        def handler(request):
            raise httpx.ReadTimeout("sensitive provider detail")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sample = await measure_ollama_request(
                client,
                endpoint="http://user:password@ollama.test/api/generate?api_key=secret",
                model="qwen3:8b",
                max_output_tokens=64,
                prompt="secret prompt",
            )

        self.assertEqual(sample.status, "failed")
        self.assertTrue(sample.timed_out)
        self.assertNotIn("sensitive", sample.model_dump_json())
        self.assertNotIn("api_key", sample.model_dump_json())

    def test_summary_reports_closed_loop_rps_and_token_metrics(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        sample = {
            "status": "success",
            "started_at": now,
            "completed_at": now,
            "request_latency_ms": 500,
            "raw_ttft_ms": 100,
            "stream_chunk_count": 3,
            "itl_p50_ms": 20,
            "itl_p95_ms": 25,
            "itl_max_ms": 30,
            "output_tokens": 10,
            "output_tokens_per_second": 20,
        }
        from evaluation.ollama_benchmark import OllamaRequestSample

        summary = summarize_ollama_benchmark(
            [OllamaRequestSample(**sample), OllamaRequestSample(**sample)],
            model="qwen3:8b",
            base_url="http://user:password@localhost:11434/private?key=secret",
            concurrency=2,
            target_rps=1.5,
            elapsed_seconds=4,
        )
        self.assertEqual(summary["offered_requests_per_second"], 1.5)
        self.assertEqual(summary["observed_requests_per_second"], 0.5)
        self.assertEqual(summary["raw_ttft_ms"]["p50"], 100)
        self.assertEqual(summary["output_tokens"], 20)
        self.assertEqual(summary["aggregate_output_tokens_per_second"], 5)
        self.assertEqual(summary["base_url"], "http://localhost:11434")


if __name__ == "__main__":
    unittest.main()
