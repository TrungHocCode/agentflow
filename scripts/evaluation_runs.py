"""Create, validate, and summarize local AgentFlow evaluation run records."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import median
from typing import Iterable, Optional
from uuid import uuid4

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = ROOT / "workspace_data" / "evaluations" / "runs"
DEFAULT_REPORTS_DIR = ROOT / "workspace_data" / "evaluations" / "reports"
sys.path.insert(0, str(ROOT))
from evaluation.record import (  # noqa: E402
    ComponentTiming,
    EvaluationRunRecord,
    LLMCallTiming,
    PerformanceMetrics,
    ResourceMetrics,
    ThroughputMetrics,
)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def git_value(*args: str) -> str:
    """Read a short Git value without making Git metadata changes."""

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def slug(value: str) -> str:
    """Convert a user-supplied identifier into a safe filename component."""

    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_").lower()
    return value or "run"


def write_record(path: Path, record: EvaluationRunRecord) -> None:
    """Write a complete JSON record using a temporary sibling file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def load_record(path: Path) -> EvaluationRunRecord:
    """Read and validate one JSON record."""

    return EvaluationRunRecord.model_validate_json(path.read_text(encoding="utf-8"))


def create_record(
    case_id: str,
    model: str,
    mode: Optional[str] = None,
    quantization: Optional[str] = None,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    prompt_revision: Optional[str] = None,
    tool_revision: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    runs_dir: Path = DEFAULT_RUNS_DIR,
) -> Path:
    """Create an in-progress record and return its path."""

    case_path = ROOT / "evaluation" / "cases" / f"{slug(case_id)}.json"
    if not case_path.is_file():
        raise ValueError(f"Unknown case '{case_id}'. Expected a case file at {case_path}.")
    case = json.loads(case_path.read_text(encoding="utf-8"))
    selected_mode = mode or case.get("mode", "live")

    started_at = utc_now()
    resolved_evaluation_id = evaluation_id or str(uuid4())
    record = EvaluationRunRecord(
        evaluation_id=resolved_evaluation_id,
        case_id=case_id,
        started_at=started_at,
        git={"branch": git_value("branch", "--show-current"), "commit": git_value("rev-parse", "HEAD")},
        configuration={
            "mode": selected_mode,
            "model": model,
            "quantization": quantization,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "prompt_revision": prompt_revision,
            "tool_revision": tool_revision,
        },
    )
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}_{slug(case_id)}_{slug(resolved_evaluation_id)}.json"
    path = runs_dir / filename
    if path.exists():
        raise FileExistsError(f"Run record already exists: {path}")
    write_record(path, record)
    return path


def finish_record(
    path: Path,
    execution_status: str,
    evaluation_verdict: str,
    agentflow_run_id: Optional[str] = None,
) -> EvaluationRunRecord:
    """Mark an in-progress record terminal and save it."""

    record = load_record(path)
    if record.execution_status != "in_progress":
        raise ValueError(f"Record is already terminal: {record.execution_status}")
    updated = record.model_copy(
        update={
            "finished_at": utc_now(),
            "execution_status": execution_status,
            "evaluation_verdict": evaluation_verdict,
            "agentflow_run_id": agentflow_run_id or record.agentflow_run_id,
        }
    )
    updated = EvaluationRunRecord.model_validate(updated.model_dump())
    write_record(path, updated)
    return updated


def percentile(values: Iterable[int | float], percentile_value: float) -> Optional[float]:
    """Return a nearest-rank percentile, or None if no values are available."""

    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, ceil(percentile_value * len(ordered)) - 1)
    return float(ordered[index])


def _display_number(value: Optional[float], suffix: str = "") -> str:
    if value is None:
        return "—"
    if float(value).is_integer():
        return f"{int(value)}{suffix}"
    return f"{value:.1f}{suffix}"


def _performance_values(records: list[EvaluationRunRecord], field: str) -> list[int]:
    return [
        value
        for record in records
        if (value := getattr(record.performance, field)) is not None
    ]


def _llm_call_timings(values: list[dict]) -> list[LLMCallTiming]:
    timings: list[LLMCallTiming] = []
    for value in values:
        latency = value.get("request_latency_ms", value.get("total_ms"))
        if latency is None:
            continue
        timings.append(
            LLMCallTiming(
                call_id=value.get("call_id"),
                component=str(value.get("component") or value.get("purpose") or "llm"),
                model=value.get("model"),
                calls=1,
                total_ms=max(0, round(float(latency))),
                ttft_ms=(
                    max(0, round(float(value["raw_ttft_ms"])))
                    if value.get("raw_ttft_ms") is not None
                    else None
                ),
                raw_ttft_ms=(
                    max(0, round(float(value["raw_ttft_ms"])))
                    if value.get("raw_ttft_ms") is not None
                    else None
                ),
                generation_ms=(
                    max(0, round(float(value["generation_ms"])))
                    if value.get("generation_ms") is not None
                    else None
                ),
                itl_p50_ms=(
                    max(0, round(float(value["itl_p50_ms"])))
                    if value.get("itl_p50_ms") is not None
                    else None
                ),
                itl_p95_ms=(
                    max(0, round(float(value["itl_p95_ms"])))
                    if value.get("itl_p95_ms") is not None
                    else None
                ),
                input_tokens=value.get("input_tokens"),
                output_tokens=value.get("output_tokens"),
                output_tokens_per_second=value.get("output_tokens_per_second"),
                model_load_ms=(
                    max(0, round(float(value["model_load_ms"])))
                    if value.get("model_load_ms") is not None
                    else None
                ),
                prompt_eval_ms=(
                    max(0, round(float(value["prompt_eval_ms"])))
                    if value.get("prompt_eval_ms") is not None
                    else None
                ),
                provider_total_ms=(
                    max(0, round(float(value["provider_total_ms"])))
                    if value.get("provider_total_ms") is not None
                    else None
                ),
                status=value.get("status"),
            )
        )
    return timings


def _merge_evaluation_llm_calls(
    existing: list[LLMCallTiming],
    incoming: list[LLMCallTiming],
) -> list[LLMCallTiming]:
    """Keep direct benchmark and runtime calls together, deduplicating stable call IDs."""

    merged: dict[str, LLMCallTiming] = {}
    for index, call in enumerate([*existing, *incoming]):
        key = call.call_id or f"anonymous-{index}"
        merged[key] = call
    return list(merged.values())


def import_ollama_benchmark(record_path: Path, benchmark_path: Path) -> EvaluationRunRecord:
    """Merge a direct Ollama benchmark JSON file into one evaluation record."""

    record = load_record(record_path)
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    model = str(benchmark.get("model") or "")
    if not model:
        raise ValueError("Benchmark JSON does not identify the model.")
    if record.configuration.model != model:
        raise ValueError(
            f"Model mismatch: evaluation record uses {record.configuration.model!r}, "
            f"benchmark uses {model!r}. Create a matching evaluation record first."
        )
    if record.performance.load_test is not None and record.performance.load_test.target != "llm":
        raise ValueError(
            "This record already contains a different load-test target. "
            "Use a separate evaluation record for API, LLM, and workflow load tests."
        )

    load_test = ThroughputMetrics(
        target="llm",
        concurrency=benchmark.get("concurrency"),
        offered_requests_per_second=benchmark.get("offered_requests_per_second"),
        observed_requests_per_second=benchmark.get("observed_requests_per_second"),
        test_duration_seconds=benchmark.get("test_duration_seconds"),
        warmup_requests=benchmark.get("warmup_requests"),
        warmup_successful_requests=benchmark.get("warmup_successful_requests"),
        warmup_duration_seconds=benchmark.get("warmup_duration_seconds"),
        accepted_runs=benchmark.get("request_count"),
        completed_runs=benchmark.get("successful_requests"),
        successful_requests=benchmark.get("successful_requests"),
        failed_requests=benchmark.get("failed_requests"),
        timed_out_requests=benchmark.get("timed_out_requests"),
        output_tokens_per_second=benchmark.get("aggregate_output_tokens_per_second"),
    )
    performance_data = record.performance.model_dump()
    performance_data["load_test"] = load_test.model_dump()
    performance_data["llm_calls"] = [
        timing.model_dump()
        for timing in _merge_evaluation_llm_calls(
            record.performance.llm_calls,
            _llm_call_timings(
                [
                    {"component": "ollama_benchmark", "model": model, **sample}
                    for sample in benchmark.get("samples") or []
                ]
            ),
        )
    ]
    resources_data = record.resources.model_dump()
    resource_values = benchmark.get("resources") or {}
    for field in (
        "cpu_utilization_percent",
        "peak_ram_mb",
        "gpu_name",
        "gpu_utilization_percent",
        "peak_vram_mb",
    ):
        if resource_values.get(field) is not None:
            resources_data[field] = resource_values[field]
    updated = EvaluationRunRecord.model_validate(
        record.model_copy(
            update={
                "performance": PerformanceMetrics.model_validate(performance_data),
                "resources": ResourceMetrics.model_validate(resources_data),
            }
        ).model_dump()
    )
    write_record(record_path, updated)
    return updated


def import_http_log_summary(
    record_path: Path,
    summary_path: Path,
    *,
    route: str | None = None,
    method: str | None = None,
) -> EvaluationRunRecord:
    """Attach one normalized API route's offline latency and RPS summary."""

    record = load_record(record_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    routes = summary.get("routes") or []
    selected = [
        item
        for item in routes
        if (route is None or item.get("route") == route)
        and (method is None or str(item.get("method", "")).upper() == method.upper())
    ]
    if len(selected) != 1:
        raise ValueError(
            "Select exactly one normalized route with --route and optionally --method; "
            f"{len(selected)} route(s) matched."
        )
    selected_route = selected[0]
    current_load_test = record.performance.load_test
    if current_load_test is not None and current_load_test.target != "api":
        raise ValueError(
            "This record already contains a different load-test target. "
            "Use a separate evaluation record for API, LLM, and workflow load tests."
        )

    latency = selected_route.get("latency_ms") or {}
    request_count = int(selected_route.get("request_count", 0))
    load_test = ThroughputMetrics(
        target="api",
        observed_requests_per_second=selected_route.get("observed_requests_per_second"),
        test_duration_seconds=summary.get("measurement_window_seconds"),
        successful_requests=selected_route.get("successful_request_count"),
        failed_requests=(
            int(selected_route.get("client_error_count", 0))
            + int(selected_route.get("server_error_count", 0))
        ),
    )
    performance_data = record.performance.model_dump()
    performance_data.update(
        {
            "api_response_ms": _safe_metric_int(latency.get("p50")),
            "api_response_p95_ms": _safe_metric_int(latency.get("p95")),
            "load_test": load_test.model_dump(),
        }
    )
    if request_count == 0:
        raise ValueError("The selected API route has no captured requests.")
    updated = EvaluationRunRecord.model_validate(
        record.model_copy(
            update={"performance": PerformanceMetrics.model_validate(performance_data)}
        ).model_dump()
    )
    write_record(record_path, updated)
    return updated


def import_run_metrics(
    record_path: Path,
    run_data: dict,
    conversation_metadata: dict | None = None,
) -> EvaluationRunRecord:
    """Merge private runtime metrics from a persisted AgentFlow run snapshot."""

    record = load_record(record_path)
    metadata = run_data.get("metadata") or run_data.get("run_metadata") or {}
    run_id = run_data.get("run_id")
    if not run_id:
        raise ValueError("Persisted run data is missing run_id.")

    timing = metadata.get("run_timing_metrics") or {}
    execution_spans = metadata.get("execution_timings") or []
    task_spans = metadata.get("task_execution_metrics") or []
    conversation_metadata = conversation_metadata or {}
    conversation_last_turn = conversation_metadata.get("last_turn") or {}
    conversation_spans = conversation_metadata.get("execution_timings") or []
    all_execution_spans = [*conversation_spans, *execution_spans]
    all_llm_call_metrics = [
        *(conversation_metadata.get("llm_call_metrics") or []),
        *(metadata.get("llm_call_metrics") or []),
    ]
    last_turn_id = conversation_last_turn.get("turn_id")
    chat_ttft = next(
        (
            sample.get("ttft_ms")
            for sample in reversed(conversation_metadata.get("chat_ttft_samples") or [])
            if sample.get("turn_id") == last_turn_id
            and isinstance(sample.get("ttft_ms"), (int, float))
        ),
        None,
    )
    tool_groups: dict[str, list[dict]] = defaultdict(list)
    for span in execution_spans:
        if span.get("operation") == "tool":
            tool_groups[str(span.get("name") or "unknown")].append(span)
    tool_timings = [
        ComponentTiming(
            name=name,
            calls=len(spans),
            total_ms=max(0, round(sum(float(span.get("duration_ms", 0)) for span in spans))),
            max_ms=max(0, round(max(float(span.get("duration_ms", 0)) for span in spans))),
            p50_ms=round(percentile([float(span.get("duration_ms", 0)) for span in spans], 0.5) or 0),
            p95_ms=round(percentile([float(span.get("duration_ms", 0)) for span in spans], 0.95) or 0),
            failures=sum(span.get("status") == "failed" for span in spans),
            timeouts=sum(
                "timeout" in str(span.get("error_type") or "").lower()
                for span in spans
            ),
        )
        for name, spans in sorted(tool_groups.items())
    ]
    node_groups: dict[str, list[dict]] = defaultdict(list)
    for span in task_spans:
        node_groups[str(span.get("node") or "unknown")].append(span)
    node_timings = [
        ComponentTiming(
            name=name,
            calls=len(spans),
            total_ms=max(0, round(sum(float(span.get("duration_ms", 0)) for span in spans))),
            max_ms=max(0, round(max(float(span.get("duration_ms", 0)) for span in spans))),
            p50_ms=round(percentile([float(span.get("duration_ms", 0)) for span in spans], 0.5) or 0),
            p95_ms=round(percentile([float(span.get("duration_ms", 0)) for span in spans], 0.95) or 0),
            failures=sum(span.get("status") == "failed" for span in spans),
        )
        for name, spans in sorted(node_groups.items())
    ]

    plan_spans = [
        float(span.get("duration_ms", 0))
        for span in all_execution_spans
        if span.get("phase") == "plan" and span.get("operation") == "llm"
    ]
    performance_data = record.performance.model_dump()
    imported_values = {
        "queue_wait_ms": _safe_metric_int(
            timing.get("queue_wait_ms", metadata.get("queue_wait_ms"))
        ),
        "workflow_execution_ms": _safe_metric_int(
            timing.get("workflow_execution_ms", run_data.get("execution_time_ms"))
        ),
        "end_to_end_ms": _safe_metric_int(
            timing.get("end_to_end_ms", metadata.get("end_to_end_ms"))
        ),
        "plan_generation_ms": _safe_metric_int(
            conversation_last_turn.get("planning_duration_ms")
        ) or (_safe_metric_int(sum(plan_spans)) if plan_spans else None),
        "critical_path_ms": _safe_metric_int(
            (metadata.get("task_metrics") or {}).get("critical_path_ms")
        ),
    }
    if chat_ttft is not None:
        imported_values["supervisor_ttft_ms"] = _safe_metric_int(chat_ttft)
    performance_data.update(
        {key: value for key, value in imported_values.items() if value is not None}
    )
    if node_timings:
        performance_data["node_timings"] = [item.model_dump() for item in node_timings]
    if tool_timings:
        performance_data["tool_timings"] = [item.model_dump() for item in tool_timings]
    if all_llm_call_metrics:
        performance_data["llm_calls"] = [
            item.model_dump()
            for item in _merge_evaluation_llm_calls(
                record.performance.llm_calls,
                _llm_call_timings(all_llm_call_metrics),
            )
        ]
    updated = EvaluationRunRecord.model_validate(
        record.model_copy(
            update={
                "agentflow_run_id": str(run_id),
                "performance": PerformanceMetrics.model_validate(performance_data),
            }
        ).model_dump()
    )
    write_record(record_path, updated)
    return updated


def _safe_metric_int(value: object) -> int | None:
    if isinstance(value, (int, float)) and value >= 0:
        return round(value)
    return None


async def import_run_from_postgres(record_path: Path, run_id: str) -> EvaluationRunRecord:
    """Read one persisted run's internal metrics from the configured PostgreSQL database."""

    backend_path = ROOT / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    from sqlalchemy import select
    from app.db.postgres_client import AsyncSessionLocal, engine
    from app.infrastructure.postgres.models import ConversationModel, RunModel

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(RunModel).where(RunModel.run_id == run_id))
            model = result.scalar_one_or_none()
            if model is None:
                raise ValueError(f"No persisted AgentFlow run found for ID {run_id!r}.")
            conversation_metadata = {}
            if model.conversation_id:
                conversation_result = await session.execute(
                    select(ConversationModel).where(
                        ConversationModel.id == model.conversation_id
                    )
                )
                conversation = conversation_result.scalar_one_or_none()
                if conversation is not None:
                    conversation_metadata = dict(
                        conversation.conversation_metadata or {}
                    )
            snapshot = {
                "run_id": model.run_id,
                "execution_time_ms": model.execution_time_ms,
                "metadata": dict(model.run_metadata or {}),
            }
        return import_run_metrics(record_path, snapshot, conversation_metadata)
    finally:
        await engine.dispose()


def render_summary(records: list[EvaluationRunRecord]) -> str:
    """Render a compact Markdown summary grouped by case and model setup."""

    grouped: dict[tuple[str, str, str, str, str], list[EvaluationRunRecord]] = defaultdict(list)
    for record in records:
        configuration = record.configuration
        generation = (
            f"commit={record.git.commit[:8]}"
            f";prompt={configuration.prompt_revision or '-'}"
            f";tools={configuration.tool_revision or '-'}"
            f";temp={configuration.temperature if configuration.temperature is not None else '-'}"
            f";max_tokens={configuration.max_output_tokens or '-'}"
        )
        key = (
            record.case_id,
            configuration.model,
            configuration.quantization or "unspecified",
            configuration.mode,
            generation,
        )
        grouped[key].append(record)

    lines = [
        "# AgentFlow Evaluation Summary",
        "",
        f"Generated: {utc_now().isoformat()}",
        "",
        "This report aggregates saved run records. In-progress records are counted but excluded from terminal metrics.",
        "",
        "## Outcomes and quality",
        "",
        "| Case | Model | Quantization | Mode | Build/config | Runs | In progress | Outcomes (C/P/F/X) | "
        "Verdicts (P/R/F) | Coverage | Valid sources | Citation coverage | Unsupported claims |",
        "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]

    for key, group in sorted(grouped.items()):
        case_id, model, quantization, mode, generation = key
        terminal = [record for record in group if record.execution_status != "in_progress"]
        requested = sum(
            record.quality.requested_dimensions or 0
            for record in terminal
            if record.quality.requested_dimensions is not None
        )
        covered = sum(
            record.quality.covered_dimensions or 0
            for record in terminal
            if record.quality.covered_dimensions is not None
        )
        coverage = 100 * covered / requested if requested else None
        sources_found = sum(
            record.quality.sources_found or 0
            for record in terminal
            if record.quality.sources_found is not None
        )
        sources_validated = sum(
            record.quality.sources_validated or 0
            for record in terminal
            if record.quality.sources_validated is not None
        )
        source_validation = 100 * sources_validated / sources_found if sources_found else None
        factual_claims = sum(
            record.quality.factual_claims or 0
            for record in terminal
            if record.quality.factual_claims is not None
        )
        cited_claims = sum(
            record.quality.claims_with_citations or 0
            for record in terminal
            if record.quality.claims_with_citations is not None
        )
        citation_coverage = 100 * cited_claims / factual_claims if factual_claims else None
        unsupported_claims = sum(
            record.quality.unsupported_claims or 0
            for record in terminal
            if record.quality.unsupported_claims is not None
        )
        outcomes = "/".join(
            str(sum(record.execution_status == status for record in terminal))
            for status in ("completed", "partial", "failed", "cancelled")
        )
        verdicts = "/".join(
            str(sum(record.evaluation_verdict == verdict for record in terminal))
            for verdict in ("pass", "needs_review", "fail")
        )
        lines.append(
            "| {case} | {model} | {quant} | {mode} | {generation} | {count} | {pending} | "
            "{outcomes} | {verdicts} | "
            "{coverage} | {source_validation} | {citation_coverage} | {unsupported_claims} |".format(
                case=case_id,
                model=model.replace("|", "\\|"),
                quant=quantization,
                mode=mode,
                generation=generation.replace("|", "\\|"),
                count=len(terminal),
                pending=len(group) - len(terminal),
                outcomes=outcomes,
                verdicts=verdicts,
                coverage=_display_number(coverage, "%"),
                source_validation=_display_number(source_validation, "%"),
                citation_coverage=_display_number(citation_coverage, "%"),
                unsupported_claims=unsupported_claims if factual_claims else "—",
            )
        )

    if not grouped:
        lines.append("| No valid run records found | — | — | — | — | 0 | 0 | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## User-visible responsiveness",
            "",
            "| Case | Model | Quantization | Mode | Build/config | Runs | p50 API | p95 API | p50 Supervisor TTFT | "
            "p50 First Progress | p50 SSE Lag | SSE Reconnects | API observed RPS |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, group in sorted(grouped.items()):
        case_id, model, quantization, mode, generation = key
        terminal = [record for record in group if record.execution_status != "in_progress"]
        api_values = _performance_values(terminal, "api_response_ms")
        api_p95_values = _performance_values(terminal, "api_response_p95_ms")
        ttft_values = _performance_values(terminal, "supervisor_ttft_ms")
        progress_values = _performance_values(terminal, "first_progress_event_ms")
        sse_lag_values = _performance_values(terminal, "sse_delivery_lag_ms")
        reconnect_values = _performance_values(terminal, "sse_reconnects")
        reconnect_count = sum(reconnect_values)
        api_rps_values = [
            record.performance.load_test.observed_requests_per_second
            for record in terminal
            if record.performance.load_test is not None
            and record.performance.load_test.target == "api"
            and record.performance.load_test.observed_requests_per_second is not None
        ]
        lines.append(
            "| {case} | {model} | {quantization} | {mode} | {generation} | {runs} | {api} | {api95} | {ttft} | "
            "{progress} | {sse_lag} | {reconnects} | {api_rps} |".format(
                case=case_id,
                model=model.replace("|", "\\|"),
                quantization=quantization,
                mode=mode,
                generation=generation.replace("|", "\\|"),
                runs=len(terminal),
                api=_display_number(median(api_values) if api_values else None, " ms"),
                api95=_display_number(median(api_p95_values) if api_p95_values else None, " ms"),
                ttft=_display_number(median(ttft_values) if ttft_values else None, " ms"),
                progress=_display_number(median(progress_values) if progress_values else None, " ms"),
                sse_lag=_display_number(median(sse_lag_values) if sse_lag_values else None, " ms"),
                reconnects=reconnect_count if reconnect_values else "—",
                api_rps=_display_number(median(api_rps_values) if api_rps_values else None),
            )
        )

    if not grouped:
        lines.append("| No valid run records found | — | — | — | — | 0 | — | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Workflow latency and throughput",
            "",
            "| Case | Model | Quantization | Mode | Build/config | Runs | p50 E2E | p95 E2E | p50 Plan | "
            "p50 Queue | p50 Execution | p50 Critical Path | Median RPS | Completed runs/min |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, group in sorted(grouped.items()):
        case_id, model, quantization, mode, generation = key
        terminal = [record for record in group if record.execution_status != "in_progress"]
        e2e_values = _performance_values(terminal, "end_to_end_ms")
        plan_values = _performance_values(terminal, "plan_generation_ms")
        queue_values = _performance_values(terminal, "queue_wait_ms")
        execution_values = _performance_values(terminal, "workflow_execution_ms")
        critical_path_values = _performance_values(terminal, "critical_path_ms")
        rps_values = [
            record.performance.load_test.observed_requests_per_second
            for record in terminal
            if record.performance.load_test is not None
            and record.performance.load_test.target == "workflow"
            and record.performance.load_test.observed_requests_per_second is not None
        ]
        runs_per_minute = [
            load_test.completed_runs * 60 / load_test.test_duration_seconds
            for record in terminal
            if (load_test := record.performance.load_test) is not None
            and load_test.target == "workflow"
            and load_test.completed_runs is not None
            and load_test.test_duration_seconds is not None
            and load_test.test_duration_seconds > 0
        ]
        lines.append(
            "| {case} | {model} | {quantization} | {mode} | {generation} | {runs} | {p50} | {p95} | "
            "{plan} | {queue} | {execution} | {critical_path} | {rps} | {run_rate} |".format(
                case=case_id,
                model=model.replace("|", "\\|"),
                quantization=quantization,
                mode=mode,
                generation=generation.replace("|", "\\|"),
                runs=len(terminal),
                p50=_display_number(median(e2e_values) if e2e_values else None, " ms"),
                p95=_display_number(percentile(e2e_values, 0.95), " ms"),
                plan=_display_number(median(plan_values) if plan_values else None, " ms"),
                queue=_display_number(median(queue_values) if queue_values else None, " ms"),
                execution=_display_number(median(execution_values) if execution_values else None, " ms"),
                critical_path=_display_number(
                    median(critical_path_values) if critical_path_values else None,
                    " ms",
                ),
                rps=_display_number(median(rps_values) if rps_values else None),
                run_rate=_display_number(median(runs_per_minute) if runs_per_minute else None),
            )
        )

    if not grouped:
        lines.append("| No valid run records found | — | — | — | — | 0 | — | — | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## LLM inference and throughput",
            "",
            "| Case | Model | Quantization | Mode | Build/config | LLM calls | p50 raw TTFT | p95 raw TTFT | "
            "p50 ITL | p95 ITL | p50 request | p95 request | p50 output tok/s | Output tokens | LLM observed RPS |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, group in sorted(grouped.items()):
        case_id, model, quantization, mode, generation = key
        terminal = [record for record in group if record.execution_status != "in_progress"]
        calls = [call for record in terminal for call in record.performance.llm_calls]
        raw_ttft_values = [call.raw_ttft_ms for call in calls if call.raw_ttft_ms is not None]
        itl_values = [call.itl_p50_ms for call in calls if call.itl_p50_ms is not None]
        itl_p95_values = [call.itl_p95_ms for call in calls if call.itl_p95_ms is not None]
        request_values = [call.total_ms for call in calls]
        tps_values = [
            call.output_tokens_per_second
            for call in calls
            if call.output_tokens_per_second is not None
        ]
        output_tokens = sum(call.output_tokens or 0 for call in calls)
        observed_rps_values = [
            record.performance.load_test.observed_requests_per_second
            for record in terminal
            if record.performance.load_test is not None
            and record.performance.load_test.target == "llm"
            and record.performance.load_test.observed_requests_per_second is not None
        ]
        lines.append(
            "| {case} | {model} | {quantization} | {mode} | {generation} | {calls} | {ttft50} | "
            "{ttft95} | {itl} | {itl95} | {request} | {request95} | {tps} | {tokens} | {rps} |".format(
                case=case_id,
                model=model.replace("|", "\\|"),
                quantization=quantization,
                mode=mode,
                generation=generation.replace("|", "\\|"),
                calls=len(calls),
                ttft50=_display_number(median(raw_ttft_values) if raw_ttft_values else None, " ms"),
                ttft95=_display_number(percentile(raw_ttft_values, 0.95), " ms"),
                itl=_display_number(median(itl_values) if itl_values else None, " ms"),
                itl95=_display_number(median(itl_p95_values) if itl_p95_values else None, " ms"),
                request=_display_number(median(request_values) if request_values else None, " ms"),
                request95=_display_number(percentile(request_values, 0.95), " ms"),
                tps=_display_number(median(tps_values) if tps_values else None),
                tokens=output_tokens if calls else "—",
                rps=_display_number(median(observed_rps_values) if observed_rps_values else None),
            )
        )
    if not grouped:
        lines.append("| No valid run records found | — | — | — | — | 0 | — | — | — | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "Outcome order: completed / partial / failed / cancelled. Verdict order: pass / needs review / fail.",
            "Coverage is the weighted ratio of covered to requested dimensions when those counts were recorded.",
            "Quantization is included in the outcomes table; group latency comparisons using matching quantization.",
            "",
        ]
    )
    return "\n".join(lines)


def summarize_runs(runs_dir: Path, output_path: Optional[Path] = None) -> tuple[Path, int, int]:
    """Summarize all valid JSON records under a directory."""

    records: list[EvaluationRunRecord] = []
    invalid_count = 0
    for path in sorted(runs_dir.rglob("*.json")) if runs_dir.exists() else []:
        try:
            records.append(load_record(path))
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as error:
            invalid_count += 1
            print(f"Skipping invalid record {path}: {error}", file=sys.stderr)

    if output_path is None:
        output_path = DEFAULT_REPORTS_DIR / f"summary-{utc_now().strftime('%Y%m%dT%H%M%SZ')}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_summary(records), encoding="utf-8")
    return output_path, len(records), invalid_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    new_parser = commands.add_parser("new", help="start a local record for one evaluation run")
    new_parser.add_argument("--case-id", required=True)
    new_parser.add_argument("--model", required=True)
    new_parser.add_argument("--quantization")
    new_parser.add_argument("--temperature", type=float)
    new_parser.add_argument("--max-output-tokens", type=int)
    new_parser.add_argument("--prompt-revision")
    new_parser.add_argument("--tool-revision")
    new_parser.add_argument("--mode", choices=("live", "replay"), help="override the case's default mode")
    new_parser.add_argument("--evaluation-id")
    new_parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)

    finish_parser = commands.add_parser("finish", help="set terminal status and verdict on a run record")
    finish_parser.add_argument("record", type=Path)
    finish_parser.add_argument(
        "--execution-status", required=True, choices=("completed", "partial", "failed", "cancelled")
    )
    finish_parser.add_argument("--verdict", required=True, choices=("pass", "needs_review", "fail"))
    finish_parser.add_argument("--agentflow-run-id")

    validate_parser = commands.add_parser("validate", help="validate a saved run record")
    validate_parser.add_argument("record", type=Path)

    summary_parser = commands.add_parser("summary", help="aggregate records into a Markdown report")
    summary_parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    summary_parser.add_argument("--output", type=Path)

    run_import_parser = commands.add_parser(
        "import-run",
        help="import internal timing metrics for an AgentFlow run from PostgreSQL",
    )
    run_import_parser.add_argument("record", type=Path)
    run_import_parser.add_argument("--run-id", required=True)

    benchmark_import_parser = commands.add_parser(
        "import-ollama-benchmark",
        help="merge direct Ollama benchmark JSON into a matching evaluation record",
    )
    benchmark_import_parser.add_argument("record", type=Path)
    benchmark_import_parser.add_argument("benchmark", type=Path)

    http_import_parser = commands.add_parser(
        "import-http-summary",
        help="attach one normalized API route's latency/RPS summary to an evaluation record",
    )
    http_import_parser.add_argument("record", type=Path)
    http_import_parser.add_argument("summary", type=Path)
    http_import_parser.add_argument("--route", help="normalized path, for example /api/v1/runs/{id}")
    http_import_parser.add_argument("--method", help="HTTP method such as GET or POST")

    schema_parser = commands.add_parser("schema", help="export the JSON Schema for the record contract")
    schema_parser.add_argument(
        "--output", type=Path, default=ROOT / "evaluation" / "run-record.schema.json"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "new":
            path = create_record(
                case_id=args.case_id,
                model=args.model,
                mode=args.mode,
                quantization=args.quantization,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
                prompt_revision=args.prompt_revision,
                tool_revision=args.tool_revision,
                evaluation_id=args.evaluation_id,
                runs_dir=args.runs_dir,
            )
            print(path)
        elif args.command == "finish":
            record = finish_record(
                args.record,
                args.execution_status,
                args.verdict,
                agentflow_run_id=args.agentflow_run_id,
            )
            print(
                f"Finished evaluation {record.evaluation_id}: {record.execution_status}, "
                f"verdict={record.evaluation_verdict}"
            )
        elif args.command == "validate":
            record = load_record(args.record)
            print(f"Valid {record.execution_status} record: {record.evaluation_id} ({record.case_id})")
        elif args.command == "summary":
            path, valid_count, invalid_count = summarize_runs(args.runs_dir, args.output)
            print(f"Wrote {path} from {valid_count} valid record(s); skipped {invalid_count} invalid record(s).")
        elif args.command == "import-run":
            record = asyncio.run(import_run_from_postgres(args.record, args.run_id))
            print(f"Imported private metrics for AgentFlow run {record.agentflow_run_id} into {args.record}")
        elif args.command == "import-ollama-benchmark":
            record = import_ollama_benchmark(args.record, args.benchmark)
            print(f"Imported {len(record.performance.llm_calls)} Ollama sample(s) into {args.record}")
        elif args.command == "import-http-summary":
            record = import_http_log_summary(
                args.record,
                args.summary,
                route=args.route,
                method=args.method,
            )
            print(f"Imported API metrics for {args.route or 'the selected route'} into {args.record}")
        elif args.command == "schema":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(EvaluationRunRecord.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(args.output)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
