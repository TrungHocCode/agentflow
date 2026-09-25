# AgentFlow Evaluation

This directory contains the versioned evaluation contract and five repeatable end-to-end research cases. Generated run records belong under `workspace_data/evaluations/`, which is ignored by Git.

## What this evaluates

- User-visible responsiveness: API response, supervisor time-to-first-token (TTFT), first progress event, and SSE delivery/reconnect behavior.
- Async execution performance: queue wait, per-node and per-tool time, workflow execution time, and end-to-end time.
- Load capacity: concurrency, offered and observed requests per second, active runs, and completed runs. RPS is meaningful only when recorded with a load-test duration and concurrency.
- Research quality: requested-dimension coverage, source validation, claim citations, unsupported claims, acceptance checks, and a human score.
- Local resource use: CPU, RAM, GPU utilization, and peak VRAM.

Metric definitions and timing boundaries are in [metrics.md](metrics.md). The record contract is defined by `EvaluationRunRecord` in `record.py`; `run-record.schema.json` is its JSON Schema export.

## Run and save one evaluation

Run the same case before and after a change, keeping the model, quantization, prompt revision, tool revision, and mode fixed where possible:

```powershell
python scripts/evaluation_runs.py new --case-id llm-benchmark-comparison --model qwen3:8b --quantization Q4_K_M --mode live --prompt-revision supervisor-source-v1 --tool-revision crawler-v1
```

The command prints a path under `workspace_data/evaluations/runs/` and captures the current branch/commit. While testing, collect measurements from the UI, backend/worker logs, and local hardware monitor; add the source URLs and whether each source supports the claim; then finish the record, linking the AgentFlow run ID if available:

```powershell
python scripts/evaluation_runs.py finish <record.json> --execution-status partial --verdict fail --agentflow-run-id <run-id>
```

Use `completed`, `partial`, `failed`, or `cancelled` for execution status. Use `pass`, `needs_review`, or `fail` for the evaluation verdict. Fill in observed timings, quality counts, resource readings, artifact paths, and notes in the JSON record before validating it:

```powershell
python scripts/evaluation_runs.py validate <record.json>
python scripts/evaluation_runs.py summary
```

`summary` writes an aggregate Markdown report to `workspace_data/evaluations/reports/` and reports p50/p95 end-to-end latency, plan/queue/execution/TTFT medians, observed RPS where load data exists, outcome and verdict counts, dimension/source/citation coverage, and unsupported-claim counts. In-progress records are reported separately and excluded from terminal aggregates. Results are grouped by case, model configuration, commit, prompt revision, and tool revision so unlike runs are not accidentally averaged together.

## Evaluation modes

- **Live E2E:** real model, tools, queue, worker, and SSE. Captures realistic latency and current source availability; record the collection time and source URLs because the web changes.
- **Replay/regression:** fixed, saved evidence inputs for checking synthesis/report behavior deterministically. Do not use replay latency as a substitute for live end-to-end latency.

The record tool does not instrument the backend automatically and does not execute a workflow. It provides a consistent run ledger while the system's direct telemetry is being built. Unknown measurements should remain `null`, not be guessed. Do not place credentials, access tokens, private user data, or full sensitive prompts in records.

## Comparing changes

For a baseline, repeat each case at least three times with the same commit, model, quantization, prompt/tool revisions, and load settings. Compare a changed commit or prompt as a separate group; keep other settings fixed. Evaluate latency and quality together: a faster run that omits requested evidence is not an improvement. `evaluation_id` identifies the test record; `agentflow_run_id` correlates it with application logs and traces. Neither belongs in metric labels.
