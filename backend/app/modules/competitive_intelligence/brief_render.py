"""Deterministic intelligence-brief Markdown rendering; no model calls, no timestamps invented."""

from app.modules.competitive_intelligence.brief_contracts import IntelligenceBrief

ADVISORY_BY_OUTCOME: dict[str, list[str]] = {
    "changes_detected": ["Review each changed section against the comparison criteria before acting.",
                         "Confirm business impact with the own-product profile context."],
    "no_change": ["No action required; the next run compares against the same baselines."],
    "baseline_created": ["No action required; baselines are established for future comparison."],
    "partial": ["Treat uncovered sources as unknown; do not assume absence of change there."],
    "rebaseline_required": ["Approve the context change explicitly before trusting new comparisons."],
    "unavailable": ["No comparison was possible; inspect fetch outcomes before rerunning."],
}


def advisory_actions(outcome: str) -> list[str]:
    return list(ADVISORY_BY_OUTCOME.get(outcome, ["Review the coverage table before acting."]))


def render_markdown(brief: IntelligenceBrief) -> str:
    """Render approved structured brief content through fixed templates only."""
    lines = [f"# Competitive Intelligence Brief", "", f"Outcome: {brief.outcome}",
             f"Coverage: {brief.quality}", f"Observation window: {brief.observed_from.isoformat()} "
             f"to {brief.observed_to.isoformat()}", "", "## Summary", "", brief.summary, ""]
    lines.extend(["## Per-source coverage", "",
                  "| Source | Outcome | Quality | Baseline | Current | Reasons |",
                  "| --- | --- | --- | --- | --- | --- |"])
    for entry in sorted(brief.source_coverage, key=lambda item: str(item.source_id)):
        lines.append(f"| {entry.source_id} | {entry.outcome} | {entry.quality} | "
                     f"{entry.baseline_snapshot_id or '-'} | {entry.current_snapshot_id or '-'} | "
                     f"{', '.join(entry.reason_codes) or '-'} |")
    lines.extend(["", "## Findings", ""])
    if not brief.findings:
        lines.append("No verified findings in this run.")
    for finding in brief.findings:
        lines.extend(["", f"### {finding.id}", "", finding.text, "",
                      f"Verification: {finding.verification}"])
        if finding.rationale:
            lines.extend(["", finding.rationale])
    lines.extend(["", "## Conflicts", ""])
    lines.append("\n".join(f"- {conflict}" for conflict in brief.conflicts) if brief.conflicts
                 else "No conflicts recorded.")
    lines.extend(["", "## Limitations", ""])
    lines.append("\n".join(f"- {limitation}" for limitation in brief.limitations) if brief.limitations
                 else "No limitations recorded.")
    lines.extend(["", "## Advisory actions", ""])
    lines.extend(f"- {action}" for action in brief.advisory_actions)
    lines.extend(["", "## Provenance", "", f"Brief: {brief.id}", f"Watchlist: {brief.watchlist_id}",
                  f"Revision: {brief.revision_id}", f"Run: {brief.run_id}",
                  f"Artifact: {brief.artifact_hash}"])
    return "\n".join(lines).rstrip("\n") + "\n"
