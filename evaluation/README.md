# AgentFlow Evaluation

This directory contains the versioned evaluation contract and five repeatable end-to-end research cases. Generated run records belong under `workspace_data/evaluations/`, which is ignored by Git.

## What this evaluates

- User-visible responsiveness: API response, supervisor time-to-first-token (TTFT), first progress event, and SSE delivery/reconnect behavior.
- Async execution performance: queue wait, per-node and per-tool time, workflow execution time, and end-to-end time.
- Load capacity: concurrency, offered and observed requests per second, active runs, and completed runs. RPS is meaningful only when recorded with a load-test duration and concurrency.
- Research quality: requested-dimension coverage, source validation, claim citations, unsupported claims, acceptance checks, and a human score.
- Local resource use: CPU, RAM, GPU utilization, and peak VRAM where captured manually or by a local monitor.

Metric definitions and timing boundaries are in [metrics.md](metrics.md). The record contract is defined by `EvaluationRunRecord` in `record.py`; `run-record.schema.json` is its JSON Schema export.

## Run and save one evaluation

Run the same case before and after a change, keeping the model, quantization, prompt revision, tool revision, and mode fixed where possible:

```powershell
python scripts/evaluation_runs.py new --case-id llm-benchmark-comparison --model qwen3:8b --quantization Q4_K_M --mode live --prompt-revision supervisor-source-v1 --tool-revision crawler-v1
```

The command prints a path under `workspace_data/evaluations/runs/` and captures the current branch/commit. The backend stores opt-in content-free LLM, task, tool, queue, and workflow timings in private PostgreSQL metadata. Import those timings into the local evaluation record after a run:

```powershell
Push-Location backend
python ..\scripts\evaluation_runs.py import-run ..\workspace_data\evaluations\runs\<record>.json --run-id <agentflow-run-id>
Pop-Location
```

The structured HTTP middleware already logs one completion record per request. To capture and summarize API latency/RPS from a local PowerShell session, redirect the backend's standard error to a private ignored file, then run:

```powershell
python -m uvicorn app.main:app --reload 2> ..\workspace_data\api-http.jsonl
python ..\scripts\summarize_http_metrics.py ..\workspace_data\api-http.jsonl --window-seconds 60
```

Set `--window-seconds` to the actual load-test observation window (not just the time between first and last request) for meaningful RPS. The summary groups by method and normalized route, removes UUIDs and query parameters, and separates server errors. SSE endpoint duration is the full stream lifetime; do not compare it to ordinary short HTTP request latency.

To attach one route's API latency and RPS to an evaluation record, select its normalized route explicitly:

```powershell
python scripts/evaluation_runs.py import-http-summary <record.json> <http-summary.json> --method GET --route "/api/v1/runs/{id}"
```

`load_test` holds one target per record, so keep API load, direct LLM load, and workflow-load observations in separate evaluation records instead of overwriting unlike test types.

Run the independent raw Ollama benchmark to isolate inference latency and LLM throughput. It supports a concurrency burst or a paced offered request rate; generated text and prompt content are never written to the result file:

```powershell
python scripts/benchmark_ollama.py --model qwen3:8b --requests 8 --concurrency 1 --sample-resources --output workspace_data/evaluations/ollama-qwen3.json
python scripts/evaluation_runs.py import-ollama-benchmark <record.json> workspace_data/evaluations/ollama-qwen3.json
```

For example, compare one-at-a-time inference with a two-request paced load using `--concurrency 2 --target-rps 0.5`. Use `--warmup-requests 1` to exclude the first model load from the measured window; leave it at zero for a cold-start-inclusive measurement. `--sample-resources` enables best-effort CPU/RAM sampling and NVIDIA `nvidia-smi` GPU/VRAM sampling during the test; it adds a small monitoring overhead. Keep cold-start and warm-model results in separate records. Direct Ollama raw TTFT includes local HTTP/client overhead; use the provider-reported `load_duration`, `prompt_eval_duration`, and `eval_duration` to understand the breakdown.

To collect per-call application metrics, set `ENABLE_EXECUTION_BENCHMARK_METRICS=true` in `backend/.env` and restart both the API and worker processes; keep it off for normal runs if the instrumentation overhead/storage is not wanted. The import command reads the application's PostgreSQL database. Run it from `backend/` when the database URL is configured in `backend/.env`, or set `POSTGRES_URL` in the process environment. It imports the linked conversation's chat/planning metrics as well as the worker run metrics when available. It does not set the evaluation verdict or execution status. After importing, add research-quality observations and any manual browser measurements, then finish the record:

```powershell
python scripts/evaluation_runs.py finish <record.json> --execution-status partial --verdict fail --agentflow-run-id <run-id>
```

Use `completed`, `partial`, `failed`, or `cancelled` for execution status. Use `pass`, `needs_review`, or `fail` for the evaluation verdict. Fill in observed timings, quality counts, resource readings, artifact paths, and notes in the JSON record before validating it:

```powershell
python scripts/evaluation_runs.py validate <record.json>
python scripts/evaluation_runs.py summary
```

`summary` writes an aggregate Markdown report to `workspace_data/evaluations/reports/` and reports p50/p95 API and end-to-end latency, planning/queue/execution/critical-path timings, raw LLM TTFT, ITL, per-call output tokens/sec, observed RPS where load data exists, outcome and verdict counts, dimension/source/citation coverage, and unsupported-claim counts. In-progress records are reported separately and excluded from terminal aggregates. Results are grouped by case, model configuration, commit, prompt revision, and tool revision so unlike runs are not accidentally averaged together.

## Evaluation modes

- **Live E2E:** real model, tools, queue, worker, and SSE. Captures realistic latency and current source availability; record the collection time and source URLs because the web changes.
- **Replay/regression:** fixed, saved evidence inputs for checking synthesis/report behavior deterministically. Do not use replay latency as a substitute for live end-to-end latency.

The evaluation record remains a local, Git-ignored ledger; it is separate from application telemetry and does not execute a workflow. Runtime instrumentation is opt-in via `ENABLE_EXECUTION_BENCHMARK_METRICS=true`. The application currently does not continuously sample queue backlog, host resources, API RPS histograms, or browser-side SSE delivery acknowledgements. Unknown measurements should remain `null`, not be guessed. Do not place credentials, access tokens, private user data, prompts, or generated reports in evaluation records.

## Comparing changes

For a baseline, repeat each case at least three times with the same commit, model, quantization, prompt/tool revisions, and load settings. Compare a changed commit or prompt as a separate group; keep other settings fixed. Evaluate latency and quality together: a faster run that omits requested evidence is not an improvement. `evaluation_id` identifies the test record; `agentflow_run_id` correlates it with application logs and traces. Neither belongs in metric labels.
