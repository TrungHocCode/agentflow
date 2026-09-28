"""Summarize JSON-formatted AgentFlow HTTP logs without exposing resource IDs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.http_log_metrics import summarize_http_records  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", type=Path)
    parser.add_argument(
        "--window-seconds",
        type=float,
        help="Actual observation-window duration; use this for short/idle test captures.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON summary output path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = []
        malformed_count = 0
        raw_log = args.log_file.read_bytes()
        encoding = "utf-16" if raw_log.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        for line in raw_log.decode(encoding).splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                malformed_count += 1
        summary = summarize_http_records(records, window_seconds=args.window_seconds)
        summary["malformed_log_line_count"] = malformed_count
        serialized = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
            print(f"Saved HTTP metric summary: {args.output}")
        else:
            print(serialized, end="")
    except (OSError, ValueError) as error:
        print(f"Could not summarize HTTP logs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
