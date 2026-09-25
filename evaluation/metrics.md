# Metrics and Evaluation Rules

## Timing boundaries

All durations are milliseconds unless the field says otherwise. Record UTC timestamps. Use `null` when a measurement is unavailable.

| Metric | Start → end | What it diagnoses |
| --- | --- | --- |
| API response | API request received → HTTP response sent | Synchronous endpoint responsiveness; for `202`, this is acceptance time, not run completion. |
| Supervisor TTFT | Supervisor LLM request sent → first generated token | Perceived start of a model response. It is not the same as receiving a progress event. |
| First progress event | User submits request → first useful SSE progress event received by the frontend | Time until the UI can show meaningful progress. |
| Plan generation | Supervisor planning starts → plan is ready for review | Build-phase duration. |
| Queue wait | Run enqueued → worker claims the run | Queue capacity and worker availability. |
| Workflow execution | Worker starts run → run reaches terminal state | Execution cost excluding queue wait. |
| End-to-end | User submits/approves run → terminal result is available | Total user wait. State which start point is used when reporting. |
| SSE delivery lag | Event created → event received by the frontend | Event transport/publishing delay, separate from LLM and workflow time. |
| Node/tool/LLM duration | Component invocation starts → component returns | Locates the slow stage and supports per-component comparison. |

For a run with known boundaries, `end_to_end_ms` should include queue wait and execution; do not add those durations to it again. API response time for an accepted background run must not be presented as workflow latency.

## Latency and throughput summaries

- Report p50, p95, and sample count for latency; p99 is useful after there are enough observations. Do not rely on averages alone.
- `observed_requests_per_second` is valid only for a defined load-test window. Record offered RPS, concurrency, duration, accepted runs, completed runs, and peak active runs alongside it.
- For background work, also compare completed runs per minute/hour and queue wait. HTTP request/s alone can hide a growing backlog.
- Compare live runs with the same case, model, quantization, prompt/tool revision, and concurrency. Use at least three repeated runs for an initial median; report variability and environment changes.

## Quality measures

- **Dimension coverage:** `covered_dimensions / requested_dimensions`. Each test case specifies what constitutes a requested dimension.
- **Source validation:** `sources_validated / sources_found`; validation means the URL resolves to the cited source and supports the claim, not merely that it appeared in search results.
- Keep exact source URLs with source class, retrieval/publication date, fetch outcome, and whether the source supports the cited claim.
- **Citation coverage:** `claims_with_citations / factual_claims`. Also manually inspect whether citations actually support the claims.
- **Unsupported claims:** count factual claims with no supplied evidence or claims contradicted by the cited source.
- **Acceptance checks:** count of case-specific checks passed out of the total.
- **Human score:** 1–5 overall quality score with a short explanation; do not use it without the component counts above.

For research comparisons, do not combine scores across benchmark variants, model settings, number of attempts, or agent scaffolds as if they were directly comparable. Missing evidence should remain explicitly missing; it must not be filled from model memory.

## Operational measures

Track run outcomes (`completed`, `partial`, `failed`, `cancelled`), API 4xx/5xx, tool timeout/error counts, SSE reconnects, queue depth/oldest age, CPU/RAM/GPU utilization, and peak VRAM. Separate cold-model-start runs from warm-model runs because local model loading can dominate early latency.

## Correlation and cardinality

Use AgentFlow's `agentflow_run_id` and trace identifiers in structured logs/traces to inspect one execution. `evaluation_id` identifies the local test record. Do not attach either unique identifier as a metric label; that creates unbounded time series. Prefer bounded dimensions such as endpoint, agent, tool, model, status, and execution mode.
