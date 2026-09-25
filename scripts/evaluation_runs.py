"""Create, validate, and summarize local AgentFlow evaluation run records."""

from __future__ import annotations

import argparse
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
from evaluation.record import EvaluationRunRecord  # noqa: E402


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
            "| Case | Model | Quantization | Mode | Build/config | Runs | p50 API | p50 Supervisor TTFT | "
            "p50 First Progress | p50 SSE Lag | SSE Reconnects |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, group in sorted(grouped.items()):
        case_id, model, quantization, mode, generation = key
        terminal = [record for record in group if record.execution_status != "in_progress"]
        api_values = _performance_values(terminal, "api_response_ms")
        ttft_values = _performance_values(terminal, "supervisor_ttft_ms")
        progress_values = _performance_values(terminal, "first_progress_event_ms")
        sse_lag_values = _performance_values(terminal, "sse_delivery_lag_ms")
        reconnect_values = _performance_values(terminal, "sse_reconnects")
        reconnect_count = sum(reconnect_values)
        lines.append(
            "| {case} | {model} | {quantization} | {mode} | {generation} | {runs} | {api} | {ttft} | "
            "{progress} | {sse_lag} | {reconnects} |".format(
                case=case_id,
                model=model.replace("|", "\\|"),
                quantization=quantization,
                mode=mode,
                generation=generation.replace("|", "\\|"),
                runs=len(terminal),
                api=_display_number(median(api_values) if api_values else None, " ms"),
                ttft=_display_number(median(ttft_values) if ttft_values else None, " ms"),
                progress=_display_number(median(progress_values) if progress_values else None, " ms"),
                sse_lag=_display_number(median(sse_lag_values) if sse_lag_values else None, " ms"),
                reconnects=reconnect_count if reconnect_values else "—",
            )
        )

    if not grouped:
        lines.append("| No valid run records found | — | — | — | — | 0 | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Workflow latency and throughput",
            "",
            "| Case | Model | Quantization | Mode | Build/config | Runs | p50 E2E | p95 E2E | p50 Plan | "
            "p50 Queue | p50 Execution | Median RPS | Completed runs/min |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for key, group in sorted(grouped.items()):
        case_id, model, quantization, mode, generation = key
        terminal = [record for record in group if record.execution_status != "in_progress"]
        e2e_values = _performance_values(terminal, "end_to_end_ms")
        plan_values = _performance_values(terminal, "plan_generation_ms")
        queue_values = _performance_values(terminal, "queue_wait_ms")
        execution_values = _performance_values(terminal, "workflow_execution_ms")
        rps_values = [
            record.performance.load_test.observed_requests_per_second
            for record in terminal
            if record.performance.load_test is not None
            and record.performance.load_test.observed_requests_per_second is not None
        ]
        runs_per_minute = [
            load_test.completed_runs * 60 / load_test.test_duration_seconds
            for record in terminal
            if (load_test := record.performance.load_test) is not None
            and load_test.completed_runs is not None
            and load_test.test_duration_seconds is not None
            and load_test.test_duration_seconds > 0
        ]
        lines.append(
            "| {case} | {model} | {quantization} | {mode} | {generation} | {runs} | {p50} | {p95} | "
            "{plan} | {queue} | {execution} | {rps} | {run_rate} |".format(
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
                rps=_display_number(median(rps_values) if rps_values else None),
                run_rate=_display_number(median(runs_per_minute) if runs_per_minute else None),
            )
        )

    if not grouped:
        lines.append("| No valid run records found | — | — | — | — | 0 | — | — | — | — | — | — | — |")

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
