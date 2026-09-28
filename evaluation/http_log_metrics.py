"""Offline summaries for AgentFlow's structured HTTP request logs."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import ceil
from typing import Any, Iterable


_UUID_RE = re.compile(
    r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])"
)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


def normalize_route(path: str) -> str:
    """Remove UUID path segments so summaries do not expose per-user resource IDs."""

    return _UUID_RE.sub("{id}", path.split("?", 1)[0])


def summarize_http_records(
    records: Iterable[dict[str, Any]],
    *,
    window_seconds: float | None = None,
) -> dict[str, Any]:
    """Summarize structured request completion logs by normalized method and route."""

    requests = [
        record
        for record in records
        if record.get("event_type") == "http_request"
        and isinstance(record.get("duration_ms"), (int, float))
        and isinstance(record.get("status_code"), int)
    ]
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in requests:
        method = str(record.get("http_method") or "UNKNOWN").upper()
        path = normalize_route(str(record.get("http_path") or "/"))
        buckets[(method, path)].append(record)

    timestamps: list[datetime] = []
    for record in requests:
        value = record.get("timestamp")
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            timestamps.append(
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)
            )
        except ValueError:
            continue
    measured_window = window_seconds
    if measured_window is None and len(timestamps) > 1:
        measured_window = max(0.0, (max(timestamps) - min(timestamps)).total_seconds())

    routes = []
    for (method, path), values in sorted(buckets.items()):
        durations = [float(value["duration_ms"]) for value in values]
        failures = sum(int(value["status_code"]) >= 500 for value in values)
        status_counts = Counter(int(value["status_code"]) for value in values)
        routes.append(
            {
                "method": method,
                "route": path,
                "request_count": len(values),
                "server_error_count": failures,
                "client_error_count": sum(
                    count for status, count in status_counts.items() if 400 <= status < 500
                ),
                "successful_request_count": sum(
                    count for status, count in status_counts.items() if 200 <= status < 300
                ),
                "status_counts": {
                    str(status): count for status, count in sorted(status_counts.items())
                },
                "latency_ms": {
                    "p50": _percentile(durations, 0.5),
                    "p95": _percentile(durations, 0.95),
                    "max": max(durations, default=None),
                },
                "observed_requests_per_second": (
                    round(len(values) / measured_window, 3)
                    if measured_window and measured_window > 0
                    else None
                ),
            }
        )

    overall_durations = [float(record["duration_ms"]) for record in requests]
    return {
        "schema_version": "1.0",
        "source": "agentflow_http_structured_logs",
        "measurement_window_seconds": measured_window,
        "request_count": len(requests),
        "observed_requests_per_second": (
            round(len(requests) / measured_window, 3)
            if measured_window and measured_window > 0
            else None
        ),
        "server_error_count": sum(int(record["status_code"]) >= 500 for record in requests),
        "latency_ms": {
            "p50": _percentile(overall_durations, 0.5),
            "p95": _percentile(overall_durations, 0.95),
            "max": max(overall_durations, default=None),
        },
        "routes": routes,
    }
