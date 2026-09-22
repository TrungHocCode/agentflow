"""Run an AgentFlow tool without starting the API, worker, or local LLM.

Examples:
    python scripts/run_tool_lab.py --tool news_crawler --input-json '{"url":"https://example.com"}'
    python scripts/run_tool_lab.py --tool web_search --input-json '{"query":"LangGraph"}'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.execution.tools.tool_runner import run_tool  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one AgentFlow tool in isolation.")
    parser.add_argument("--tool", required=True, help="Registered tool name.")
    parser.add_argument(
        "--input-json",
        default="{}",
        help="Tool arguments as a JSON object.",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help="Optional JSON file containing tool arguments.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        raw_input = args.input_file.read_text(encoding="utf-8") if args.input_file else args.input_json
        arguments = json.loads(raw_input)
        if not isinstance(arguments, dict):
            raise ValueError("Tool input must be a JSON object.")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    result = run_tool(args.tool, arguments)
    print(json.dumps(result.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
