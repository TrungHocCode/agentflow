"""Replay the CI fixed-domain scenario suite and write measured release evidence.

Deterministic: no network, no model, no clock dependence beyond elapsed timings.
Exit status is non-zero when any acceptance fixture mismatches, so CI gates on it.
Reports land under workspace_data/evaluation/ci/ (private, ignored).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.modules.competitive_intelligence.evaluation import (  # noqa: E402
    FIXTURE_VERSION, load_fixtures, score_suite, static_pipeline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", default="workspace_data/evaluation/ci")
    parser.add_argument("--fixtures", default="evaluation/ci_scenarios.json")
    args = parser.parse_args()
    observed_at = datetime(2026, 10, 8, tzinfo=timezone.utc)
    fixtures = load_fixtures(ROOT / args.fixtures)
    scored = score_suite(fixtures, observed_at)
    static = static_pipeline(fixtures, observed_at)
    report = {
        "fixture_version": FIXTURE_VERSION,
        "generated_at": observed_at.isoformat(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "disclaimer": ("Deterministic replay only: no live sources, no model calls, no hardware claims. "
                       "Live-model acceptance (4B) and human review time are separate manual-gate steps."),
        "full_pipeline": scored,
        "static_baseline": static,
        "comparison": {
            "full_matched": scored["passed"], "static_matched": static["matched_scenarios"],
            "total": scored["scenarios"],
            "note": ("Both pipelines share section diff; the full pipeline additionally governs rounds, "
                     "coverage, verification tasks and budgets, which static replay cannot exercise."),
        },
    }
    report_dir = ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "ci-scenarios.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# CI scenario replay — {scored['passed']}/{scored['scenarios']} passed",
             f"Fixture version: {FIXTURE_VERSION}", "",
             f"Candidate precision by scenario: {scored['candidate_precision_by_scenario']}",
             f"Candidate recall: {scored['candidate_recall']}",
             f"False alerts: {scored['false_alerts']}",
             f"Replay wall-clock: {scored['elapsed_ms']} ms (harness overhead only, not a latency claim)", "",
             "## Static baseline comparison", "",
             f"Static matched {static['matched_scenarios']}/{static['total']} scenarios.", "",
             "## Failed scenarios", ""]
    lines.extend(f"- {scenario_id}" for scenario_id in scored["failed"] or ["none"])
    (report_dir / "ci-scenarios.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"CI scenarios: {scored['passed']}/{scored['scenarios']} passed; "
          f"false alerts {scored['false_alerts']}; report in {report_dir}")
    return 0 if not scored["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
