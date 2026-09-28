"""Raw Ollama timing must preserve the stream and never retain its contents."""

import asyncio
import json
import os
import sys
import unittest
from time import perf_counter
from typing import Any, AsyncIterator
from unittest.mock import patch

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_ollama import ChatOllama

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.instrumented_ollama import InstrumentedChatOllama
from app.execution.llm import get_llm
from app.execution.model_router import InferencePurpose
from app.shared.ollama_timing import OllamaTurnTiming, active_ollama_turn


class TestOllamaRawTiming(unittest.IsolatedAsyncioTestCase):
    async def test_raw_stream_records_thinking_before_content_without_recording_text(self) -> None:
        chunks = [
            {"message": {"content": "", "thinking": ""}, "done": False},
            {"message": {"thinking": "private reasoning"}, "done": False},
            {"message": {"content": "private answer"}, "done": False},
            {
                "message": {"content": ""},
                "done": True,
                "total_duration": 90_000_000,
                "load_duration": 10_000_000,
                "prompt_eval_duration": 20_000_000,
                "eval_duration": 60_000_000,
                "prompt_eval_count": 120,
                "eval_count": 25,
            },
        ]

        async def fake_stream(
            _model: ChatOllama,
            _messages: list[BaseMessage],
            _stop: list[str] | None = None,
            **_kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            for chunk in chunks:
                yield chunk

        model = InstrumentedChatOllama(model="qwen3:8b")
        trace = OllamaTurnTiming(turn_id="turn-1", turn_started_at=perf_counter())
        token = active_ollama_turn.set(trace)
        try:
            with patch.object(ChatOllama, "_acreate_chat_stream", fake_stream):
                returned = [
                    chunk async for chunk in model._acreate_chat_stream(
                        [HumanMessage(content="secret prompt")]
                    )
                ]
        finally:
            active_ollama_turn.reset(token)

        self.assertEqual(returned, chunks)
        self.assertEqual(len(trace.requests), 1)
        sample = trace.snapshot()[0]
        self.assertEqual(sample["status"], "completed")
        self.assertEqual(sample["model"], "qwen3:8b")
        self.assertIsNotNone(sample["first_chunk_ms"])
        self.assertIsNotNone(sample["raw_ttft_ms"])
        self.assertLessEqual(sample["first_chunk_ms"], sample["raw_ttft_ms"])
        self.assertIsNotNone(sample["first_thinking_ms"])
        self.assertIsNotNone(sample["first_content_ms"])
        self.assertLessEqual(sample["raw_ttft_ms"], sample["first_content_ms"])
        self.assertEqual(sample["server_total_ms"], 90.0)
        self.assertEqual(sample["load_ms"], 10.0)
        self.assertEqual(sample["prompt_eval_ms"], 20.0)
        self.assertEqual(sample["eval_ms"], 60.0)
        self.assertEqual(sample["eval_count"], 25)
        self.assertNotIn("private", json.dumps(sample))
        self.assertNotIn("secret prompt", json.dumps(sample))

    async def test_failed_stream_keeps_partial_timing_and_propagates_error(self) -> None:
        async def failing_stream(
            _model: ChatOllama,
            _messages: list[BaseMessage],
            _stop: list[str] | None = None,
            **_kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            yield {"message": {"thinking": "private reasoning"}, "done": False}
            raise RuntimeError("Ollama disconnected")

        model = InstrumentedChatOllama(model="qwen3:8b")
        trace = OllamaTurnTiming(turn_id="turn-2", turn_started_at=perf_counter())
        token = active_ollama_turn.set(trace)
        try:
            with patch.object(ChatOllama, "_acreate_chat_stream", failing_stream):
                with self.assertRaisesRegex(RuntimeError, "Ollama disconnected"):
                    _ = [chunk async for chunk in model._acreate_chat_stream([HumanMessage(content="hello")])]
        finally:
            active_ollama_turn.reset(token)

        self.assertEqual(trace.requests[0].status, "failed")
        self.assertIsNotNone(trace.requests[0].raw_ttft_ms)
        self.assertIsNotNone(trace.requests[0].elapsed_ms)

    async def test_without_active_turn_stream_is_unchanged(self) -> None:
        async def fake_stream(
            _model: ChatOllama,
            _messages: list[BaseMessage],
            _stop: list[str] | None = None,
            **_kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            yield {"message": {"content": "Hello"}, "done": True}

        model = InstrumentedChatOllama(model="qwen3:8b")
        self.assertIsNone(active_ollama_turn.get())
        with patch.object(ChatOllama, "_acreate_chat_stream", fake_stream):
            chunks = [chunk async for chunk in model._acreate_chat_stream([HumanMessage(content="hello")])]
        self.assertEqual(chunks[0]["message"]["content"], "Hello")

    async def test_concurrent_turns_keep_separate_timings(self) -> None:
        async def fake_stream(
            _model: ChatOllama,
            _messages: list[BaseMessage],
            _stop: list[str] | None = None,
            **_kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            await asyncio.sleep(0)
            yield {"message": {"content": "private answer"}, "done": True}

        async def run_turn(turn_id: str) -> OllamaTurnTiming:
            trace = OllamaTurnTiming(turn_id=turn_id, turn_started_at=perf_counter())
            token = active_ollama_turn.set(trace)
            try:
                model = InstrumentedChatOllama(model="qwen3:8b")
                _ = [chunk async for chunk in model._acreate_chat_stream([HumanMessage(content=turn_id)])]
            finally:
                active_ollama_turn.reset(token)
            return trace

        with patch.object(ChatOllama, "_acreate_chat_stream", fake_stream):
            first, second = await asyncio.gather(run_turn("turn-a"), run_turn("turn-b"))

        self.assertEqual(len(first.requests), 1)
        self.assertEqual(len(second.requests), 1)
        self.assertEqual(first.requests[0].status, "completed")
        self.assertEqual(second.requests[0].status, "completed")
        self.assertIsNot(first.requests[0], second.requests[0])
        self.assertIsNone(active_ollama_turn.get())

    async def test_llm_factory_uses_the_instrumented_client(self) -> None:
        self.assertIsInstance(get_llm(purpose=InferencePurpose.PLANNER), InstrumentedChatOllama)
