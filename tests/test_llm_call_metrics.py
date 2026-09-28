from __future__ import annotations

import unittest
from datetime import datetime, timezone
import os
import sys
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.shared.llm_call_metrics import (
    LLMCallMetric,
    LLMCallObserver,
    merge_llm_call_metrics,
    summarize_llm_call_metrics,
)


class TestLLMCallMetrics(unittest.IsolatedAsyncioTestCase):
    async def test_observer_captures_stream_timing_usage_and_never_content(self):
        with patch(
            "app.shared.llm_call_metrics.perf_counter",
            side_effect=[10.0, 10.1, 10.15, 10.4],
        ):
            observer = LLMCallObserver(
                call_id="call-1",
                component="supervisor",
                purpose="planner",
                model="qwen3:8b",
            )
            await observer.on_llm_new_token("first confidential token")
            await observer.on_llm_new_token("second confidential token")
            await observer.on_llm_end(
                LLMResult(
                    generations=[[
                        ChatGeneration(
                            message=AIMessage(
                                content="must not be persisted",
                                usage_metadata={
                                    "input_tokens": 12,
                                    "output_tokens": 5,
                                    "total_tokens": 17,
                                },
                            )
                        )
                    ]]
                )
            )
            metric = observer.to_metric(status="success")

        self.assertAlmostEqual(metric.request_latency_ms, 400)
        self.assertAlmostEqual(metric.raw_ttft_ms, 100)
        self.assertAlmostEqual(metric.itl_p50_ms, 50)
        self.assertEqual(metric.input_tokens, 12)
        self.assertEqual(metric.output_tokens, 5)
        self.assertAlmostEqual(metric.output_tokens_per_second, 100)
        self.assertNotIn("confidential", metric.model_dump_json())
        self.assertNotIn("must not be persisted", metric.model_dump_json())

    async def test_observer_reads_ollama_provider_durations(self):
        observer = LLMCallObserver(
            call_id="call-2",
            component="worker",
            purpose="worker",
            model="qwen3:8b",
        )
        await observer.on_llm_end(
            LLMResult(
                generations=[[
                    ChatGeneration(
                        message=AIMessage(content="ok"),
                        generation_info={
                            "total_duration": 200_000_000,
                            "load_duration": 10_000_000,
                            "prompt_eval_count": 20,
                            "prompt_eval_duration": 40_000_000,
                            "eval_count": 5,
                            "eval_duration": 100_000_000,
                        },
                    )
                ]]
            )
        )
        metric = observer.to_metric(status="success")
        self.assertEqual(metric.provider_total_ms, 200)
        self.assertEqual(metric.model_load_ms, 10)
        self.assertEqual(metric.prompt_eval_ms, 40)
        self.assertEqual(metric.generation_ms, 100)
        self.assertEqual(metric.input_tokens, 20)
        self.assertEqual(metric.output_tokens, 5)
        self.assertEqual(metric.output_tokens_per_second, 50)

    def test_merge_deduplicates_resumed_call_and_summary_aggregates(self):
        metric = LLMCallMetric(
            call_id="call-3",
            component="supervisor",
            purpose="planner",
            model="qwen3:8b",
            status="success",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            request_latency_ms=1000,
            raw_ttft_ms=200,
            itl_p50_ms=25,
            itl_p95_ms=40,
            generation_ms=500,
            input_tokens=10,
            output_tokens=20,
            output_tokens_per_second=40,
        ).model_dump(mode="json")
        merged = merge_llm_call_metrics([metric], [metric])
        summary = summarize_llm_call_metrics(merged)
        self.assertEqual(len(merged), 1)
        self.assertEqual(summary["call_count"], 1)
        self.assertEqual(summary["output_tokens"], 20)
        self.assertEqual(summary["output_tokens_per_second"], 40)


if __name__ == "__main__":
    unittest.main()
