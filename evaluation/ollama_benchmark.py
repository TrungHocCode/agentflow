"""Direct, content-free Ollama inference benchmark helpers."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from math import ceil
from statistics import median
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from evaluation.resource_sampling import SystemResourceSampler, summarize_resource_samples


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


class OllamaRequestSample(BaseModel):
    """Timing and provider statistics for one raw Ollama generate request."""

    call_id: str = Field(default_factory=lambda: str(uuid4()))
    status: str
    error_type: str | None = None
    timed_out: bool = False
    started_at: datetime
    completed_at: datetime
    request_latency_ms: float = Field(ge=0)
    raw_ttft_ms: float | None = Field(default=None, ge=0)
    stream_chunk_count: int = Field(default=0, ge=0)
    itl_p50_ms: float | None = Field(default=None, ge=0)
    itl_p95_ms: float | None = Field(default=None, ge=0)
    itl_max_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model_load_ms: float | None = Field(default=None, ge=0)
    prompt_eval_ms: float | None = Field(default=None, ge=0)
    generation_ms: float | None = Field(default=None, ge=0)
    provider_total_ms: float | None = Field(default=None, ge=0)
    output_tokens_per_second: float | None = Field(default=None, ge=0)


async def measure_ollama_request(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    model: str,
    max_output_tokens: int,
    prompt: str,
) -> OllamaRequestSample:
    """Measure a streamed /api/generate call without retaining prompt or response text."""

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    chunk_times: list[float] = []
    provider_stats: dict[str, int | float] = {}
    try:
        async with client.stream(
            "POST",
            endpoint,
            json={
                "model": model,
                "prompt": prompt,
                "stream": True,
                "options": {"num_predict": max_output_tokens},
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                text_chunk = payload.get("response")
                if isinstance(text_chunk, str) and text_chunk:
                    chunk_times.append(time.perf_counter())
                for field in (
                    "prompt_eval_count",
                    "eval_count",
                    "total_duration",
                    "load_duration",
                    "prompt_eval_duration",
                    "eval_duration",
                ):
                    value = payload.get(field)
                    if isinstance(value, (int, float)) and value >= 0:
                        provider_stats[field] = value

        completed = time.perf_counter()
        duration_ms = round((completed - started) * 1000, 3)
        intervals = [
            (right - left) * 1000
            for left, right in zip(chunk_times, chunk_times[1:])
        ]
        generation_ms = _nanoseconds_to_ms(provider_stats.get("eval_duration"))
        output_tokens = _nonnegative_int(provider_stats.get("eval_count"))
        output_tps = (
            round(output_tokens * 1000 / generation_ms, 3)
            if output_tokens is not None and generation_ms and generation_ms > 0
            else None
        )
        return OllamaRequestSample(
            status="success",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            request_latency_ms=duration_ms,
            raw_ttft_ms=(
                round((chunk_times[0] - started) * 1000, 3)
                if chunk_times
                else None
            ),
            stream_chunk_count=len(chunk_times),
            itl_p50_ms=_percentile(intervals, 0.5),
            itl_p95_ms=_percentile(intervals, 0.95),
            itl_max_ms=round(max(intervals), 3) if intervals else None,
            input_tokens=_nonnegative_int(provider_stats.get("prompt_eval_count")),
            output_tokens=output_tokens,
            model_load_ms=_nanoseconds_to_ms(provider_stats.get("load_duration")),
            prompt_eval_ms=_nanoseconds_to_ms(provider_stats.get("prompt_eval_duration")),
            generation_ms=generation_ms,
            provider_total_ms=_nanoseconds_to_ms(provider_stats.get("total_duration")),
            output_tokens_per_second=output_tps,
        )
    except Exception as exc:
        completed = time.perf_counter()
        return OllamaRequestSample(
            status="failed",
            error_type=type(exc).__name__,
            timed_out=isinstance(exc, httpx.TimeoutException),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            request_latency_ms=round((completed - started) * 1000, 3),
            raw_ttft_ms=(
                round((chunk_times[0] - started) * 1000, 3)
                if chunk_times
                else None
            ),
            stream_chunk_count=len(chunk_times),
            itl_p50_ms=_percentile(
                [(b - a) * 1000 for a, b in zip(chunk_times, chunk_times[1:])],
                0.5,
            ),
        )


def _nanoseconds_to_ms(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return round(float(value) / 1_000_000, 3)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    return None


def summarize_ollama_benchmark(
    samples: list[OllamaRequestSample],
    *,
    model: str,
    base_url: str,
    concurrency: int,
    target_rps: float | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Create a JSON-safe load summary. No prompts or generated text are returned."""

    succeeded = [sample for sample in samples if sample.status == "success"]
    latencies = [sample.request_latency_ms for sample in samples]
    ttfts = [sample.raw_ttft_ms for sample in samples if sample.raw_ttft_ms is not None]
    itls = [sample.itl_p50_ms for sample in samples if sample.itl_p50_ms is not None]
    tps_values = [
        sample.output_tokens_per_second
        for sample in succeeded
        if sample.output_tokens_per_second is not None
    ]
    output_tokens = sum(sample.output_tokens or 0 for sample in succeeded)
    parsed_base_url = httpx.URL(base_url)
    host = f"[{parsed_base_url.host}]" if ":" in parsed_base_url.host else parsed_base_url.host
    safe_base_url = f"{parsed_base_url.scheme}://{host}"
    if parsed_base_url.port is not None:
        safe_base_url += f":{parsed_base_url.port}"
    return {
        "schema_version": "1.0",
        "target": "ollama_llm",
        "model": model,
        "base_url": safe_base_url,
        "concurrency": concurrency,
        "offered_requests_per_second": target_rps,
        "observed_requests_per_second": (
            round(len(samples) / elapsed_seconds, 3) if elapsed_seconds > 0 else None
        ),
        "test_duration_seconds": round(elapsed_seconds, 3),
        "request_count": len(samples),
        "successful_requests": len(succeeded),
        "failed_requests": len(samples) - len(succeeded),
        "timed_out_requests": sum(sample.timed_out for sample in samples),
        "request_latency_ms": {
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies, default=None),
        },
        "raw_ttft_ms": {
            "p50": _percentile(ttfts, 0.5),
            "p95": _percentile(ttfts, 0.95),
        },
        "itl_chunk_interval_ms": {
            "p50_of_request_p50": _percentile(itls, 0.5),
            "p95_of_request_p50": _percentile(itls, 0.95),
        },
        "output_tokens": output_tokens,
        "aggregate_output_tokens_per_second": (
            round(output_tokens / elapsed_seconds, 3) if elapsed_seconds > 0 else None
        ),
        "per_request_output_tokens_per_second_p50": (
            round(median(tps_values), 3) if tps_values else None
        ),
        "samples": [sample.model_dump(mode="json") for sample in samples],
    }


async def execute_ollama_benchmark(
    *,
    base_url: str,
    model: str,
    request_count: int,
    concurrency: int,
    max_output_tokens: int,
    timeout_seconds: float,
    target_rps: float | None = None,
    warmup_requests: int = 0,
    sample_resources: bool = False,
    prompt: str = "In three concise paragraphs, explain how a database index improves query performance.",
) -> dict[str, Any]:
    """Run a paced or burst-concurrency benchmark against Ollama's raw API."""

    if request_count < 1 or concurrency < 1 or max_output_tokens < 1:
        raise ValueError("request_count, concurrency, and max_output_tokens must be positive")
    if timeout_seconds <= 0 or warmup_requests < 0:
        raise ValueError("timeout_seconds must be positive and warmup_requests cannot be negative")
    if target_rps is not None and target_rps <= 0:
        raise ValueError("target_rps must be positive")

    normalized_base_url = base_url.rstrip("/")
    parsed_url = httpx.URL(normalized_base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
        raise ValueError("base_url must be an HTTP(S) origin")
    endpoint = f"{normalized_base_url}/api/generate"
    timeout = httpx.Timeout(timeout_seconds)
    semaphore = asyncio.Semaphore(concurrency)
    samples: list[OllamaRequestSample | None] = [None] * request_count
    warmup_samples: list[OllamaRequestSample] = []
    resource_samples: list[dict[str, Any]] = []
    resource_stop = asyncio.Event()
    resource_sampler = SystemResourceSampler()
    started = 0.0

    async def sample_resources_periodically() -> None:
        while not resource_stop.is_set():
            try:
                resource_samples.append(await resource_sampler.sample())
            except Exception:
                # A monitoring failure must not fail or distort the inference benchmark.
                resource_samples.append({})
            try:
                await asyncio.wait_for(resource_stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    resource_task: asyncio.Task | None = None
    warmup_started = time.perf_counter()
    benchmark_finished = 0.0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for _ in range(warmup_requests):
                warmup_samples.append(
                    await measure_ollama_request(
                        client,
                        endpoint=endpoint,
                        model=model,
                        max_output_tokens=max_output_tokens,
                        prompt=prompt,
                    )
                )
            warmup_elapsed = max(0.0, time.perf_counter() - warmup_started)
            started = time.perf_counter()
            if sample_resources:
                resource_task = asyncio.create_task(sample_resources_periodically())

            async def issue(index: int) -> None:
                if target_rps is not None:
                    due_at = started + index / target_rps
                    await asyncio.sleep(max(0.0, due_at - time.perf_counter()))
                async with semaphore:
                    samples[index] = await measure_ollama_request(
                        client,
                        endpoint=endpoint,
                        model=model,
                        max_output_tokens=max_output_tokens,
                        prompt=prompt,
                    )

            await asyncio.gather(*(issue(index) for index in range(request_count)))
            benchmark_finished = time.perf_counter()
    finally:
        if resource_task is not None:
            resource_stop.set()
            await resource_task

    elapsed = max(0.0, benchmark_finished - started)
    completed_samples = [sample for sample in samples if sample is not None]
    summary = summarize_ollama_benchmark(
        completed_samples,
        model=model,
        base_url=normalized_base_url,
        concurrency=concurrency,
        target_rps=target_rps,
        elapsed_seconds=elapsed,
    )
    summary["warmup_requests"] = warmup_requests
    summary["warmup_successful_requests"] = sum(
        sample.status == "success" for sample in warmup_samples
    )
    summary["warmup_duration_seconds"] = round(warmup_elapsed, 3)
    if sample_resources:
        summary["resources"] = summarize_resource_samples(resource_samples)
    return summary
