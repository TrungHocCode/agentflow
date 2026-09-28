"""Run a direct, private Ollama inference benchmark and save JSON results."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env", override=False)
sys.path.insert(0, str(ROOT))

from evaluation.ollama_benchmark import execute_ollama_benchmark  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("OLLAMA_BASE_URL", os.getenv("LLM_BASE_URL", "http://localhost:11434")),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_WORKER_MODEL", "qwen3:8b"),
        help="Installed Ollama model tag, such as qwen3:8b.",
    )
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument(
        "--warmup-requests",
        type=int,
        default=0,
        help="Run and discard warm-up requests before the measured load window.",
    )
    parser.add_argument(
        "--target-rps",
        type=float,
        help="Optional paced arrival rate; omit to send a concurrent burst.",
    )
    parser.add_argument(
        "--sample-resources",
        action="store_true",
        help="Sample host CPU/RAM and NVIDIA GPU utilization during the benchmark.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser


async def run(args: argparse.Namespace) -> dict:
    return await execute_ollama_benchmark(
        base_url=args.base_url,
        model=args.model,
        request_count=args.requests,
        concurrency=args.concurrency,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout_seconds,
        target_rps=args.target_rps,
        warmup_requests=args.warmup_requests,
        sample_resources=args.sample_resources,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (OSError, ValueError, httpx.InvalidURL) as error:
        print(f"Benchmark could not start: {error}", file=sys.stderr)
        return 1

    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Saved private benchmark record: {args.output}")
    else:
        print(serialized, end="")
    return 0 if result["failed_requests"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
