# Metrics and Evaluation Rules

## Timing boundaries

All durations are milliseconds unless the field says otherwise. Record UTC timestamps. Use `null` when a measurement is unavailable.

| Metric | Start → end | What it diagnoses |
| --- | --- | --- |
| API response | API request received → HTTP response sent | Synchronous endpoint responsiveness; for `202`, this is acceptance time, not run completion. |
| Raw LLM TTFT | Ollama request begins → first non-empty generated stream chunk arrives | Inference-boundary latency; measured either through LangChain callbacks during application calls or directly against Ollama's streaming API in the isolated benchmark. It excludes the browser and most application orchestration. |
| Chat TTFT | Backend begins processing a conversation message → first assistant token emitted to the chat event stream | User-visible server-side responsiveness; includes backend setup and orchestration before the first token. It is not raw Ollama TTFT. |
| First progress event | User submits request → first useful SSE progress event received by the frontend | Time until the UI can show meaningful progress. |
| Plan generation | Backend begins processing the current chat turn → the proposed answer/plan is stored for review | Build-phase duration; unlike raw LLM latency, includes orchestration and persistence leading up to the saved draft. |
| Queue wait | Run enqueued → worker claims the run | Queue capacity and worker availability. |
| Workflow execution | Worker starts run → run reaches terminal state | Execution cost excluding queue wait. |
| End-to-end | User submits/approves run → terminal result is available | Total user wait. State which start point is used when reporting. |
| SSE delivery lag | Event created → event received by the frontend | Event transport/publishing delay, separate from LLM and workflow time. |
| Node/tool/LLM duration | Component invocation starts → component returns | Locates the slow stage and supports per-component comparison. |
| Inter-token latency (ITL) | One generated token/chunk observed → the next token/chunk observed | Streaming smoothness. AgentFlow records callback chunk intervals; the direct Ollama harness records non-empty response chunk intervals. Treat these as chunk-level approximations unless the provider confirms one chunk per token. |
| Output tokens/sec | Provider-reported output tokens ÷ generation duration (first token → completion, excluding TTFT) | Decode speed; keep separate from end-to-end tokens/sec. |

For a run with known boundaries, `end_to_end_ms` should include queue wait and execution; do not add those durations to it again. API response time for an accepted background run must not be presented as workflow latency.

## Latency and throughput summaries

- Report p50, p95, and sample count for latency; p99 is useful after there are enough observations. Do not rely on averages alone.
- `observed_requests_per_second` is valid only for a defined load-test window. Record the target (`api`, `llm`, or `workflow`), offered RPS, concurrency, duration, accepted/completed work, failures, timeouts, and peak active runs alongside it. The direct Ollama harness reports an LLM-targeted throughput figure; it is not API or workflow RPS.
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

Current opt-in runtime instrumentation stores per-call LLM timings, task/node durations, tool timings and timeouts, queue wait, worker execution, critical path, and end-to-end run time as private database metadata. The chat turn stores its full planning duration and first-token latency. Structured HTTP logs carry request duration, method, route path, and status; `scripts/summarize_http_metrics.py` can derive route-level latency and request RPS from a captured log window. The direct Ollama harness optionally samples host CPU/RAM and NVIDIA GPU/VRAM. These metrics are stripped from public API response metadata. SSE delivery lag still requires a client acknowledgement timestamp; do not substitute backend publish time and label it delivery lag. Queue depth/oldest age and host resources are not continuously sampled by the application. Separate cold-model-start runs from warm-model runs because local model loading can dominate early latency. Provider usage fields that are unavailable must remain `null`; do not estimate token counts from characters.

The direct benchmark stores only counts, timestamps, timings, and error types. It does not persist the benchmark prompt, generated content, HTTP response bodies, or credentials. It uses the Ollama `/api/generate` streaming endpoint; output-token throughput comes from Ollama's final `eval_count` and `eval_duration` fields, while ITL is based on observed non-empty stream chunks.

## Correlation and cardinality

Use AgentFlow's `agentflow_run_id` and trace identifiers in structured logs/traces to inspect one execution. `evaluation_id` identifies the local test record. Do not attach either unique identifier as a metric label; that creates unbounded time series. Prefer bounded dimensions such as endpoint, agent, tool, model, status, and execution mode.
