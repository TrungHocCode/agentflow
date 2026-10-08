"""Deterministic snapshot normalization and section-aware diff.

No LLM, no network, no clock. Normalization is whitespace/structural only: numbers,
currency, units, regions, plan names and qualifiers are preserved byte-for-byte so a
qualified pricing edit can never be normalized away.
"""

import hashlib
import re
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from app.modules.competitive_intelligence.models import SourceKind, digest
from app.modules.competitive_intelligence.snapshot_contracts import (
    CAPTURE_POLICY, DIFF_ALGORITHM_VERSION, MAX_EXCERPT_CHARS, MAX_SECTIONS, MIN_ELIGIBLE_CHARS,
    NORMALIZATION_VERSION, ChangeCandidate, ComparisonOutcome, RunSourceComparison, SourceSnapshot,
)

_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
# Conservative access-wall phrases; matching only ever marks a capture ineligible
# (safe direction: delays a baseline, never invents a change). Refine in CI-P6.
_CHALLENGE_PHRASES = ("sign in to", "log in to", "login to continue", "verify you are human", "captcha",
                      "access denied", "please verify")


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None
    text: str
    start: int
    end: int


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def context_hash(requested_url: str, final_url: str, language: str | None, region: str | None,
                 policy: str = CAPTURE_POLICY) -> str:
    """Identity of the comparison context; a context change forces rebaselining, never a change claim."""
    return digest({"requested_host": requested_url.split("/")[2].lower() if "://" in requested_url else "",
                   "final_url": final_url, "language": language, "region": region, "policy": policy})


def normalize_captured(text: str, source_kind: SourceKind,
                       normalization_version: str = NORMALIZATION_VERSION) -> str:
    """Collapse line endings and blank runs; per-kind strategies are recorded for CI-P6 evidence."""
    if normalization_version != NORMALIZATION_VERSION:
        raise ValueError(f"Unsupported normalization version: {normalization_version}")
    _ = source_kind  # Kind-independent in v1; recorded on the snapshot for future strategies.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    stripped = [line.rstrip(" \t") for line in lines]
    # Blank lines carry no business meaning; dropping them (instead of collapsing)
    # makes blank-line-only edits compare identical while preserving every word,
    # number, currency symbol and qualifier on the remaining lines.
    return "\n".join(line for line in stripped if line)


def split_sections(normalized: str) -> list[Section]:
    """Split on ATX headings; text before the first heading is the preamble (key None)."""
    matches = list(_HEADING.finditer(normalized))
    if not matches:
        return [Section(key=None, text=normalized, start=0, end=len(normalized))] if normalized else []
    sections: list[Section] = []
    if matches[0].start() > 0:
        sections.append(Section(key=None, text=normalized[:matches[0].start()].rstrip("\n"),
                                start=0, end=matches[0].start()))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        sections.append(Section(key=match.group(2).strip(), text=normalized[match.start():end].rstrip("\n"),
                                start=match.start(), end=end))
    if len(sections) > MAX_SECTIONS:
        head, tail = sections[:MAX_SECTIONS - 1], sections[MAX_SECTIONS - 1:]
        merged = "\n".join(section.text for section in tail)
        head.append(Section(key=tail[0].key, text=merged, start=tail[0].start, end=tail[-1].end))
        return head
    return sections


def quality_gate(text: str, final_url: str | None, http_status: int | None, truncated: bool,
                 source_kind: SourceKind) -> tuple[str, list[str]]:
    """Decide snapshot eligibility; ineligible captures stay comparable history, never baselines."""
    _ = (final_url, source_kind)  # Recorded for kind-specific thresholds refined in CI-P6.
    if not text.strip():
        return "ineligible", ["empty_capture"]
    if truncated:
        return "ineligible", ["truncated_capture"]
    lowered = text.lower()
    if any(phrase in lowered for phrase in _CHALLENGE_PHRASES):
        return "ineligible", ["challenge_page"]
    if len(text) < MIN_ELIGIBLE_CHARS:
        return "ineligible", ["below_minimum_length"]
    if http_status is not None and http_status != 200:
        return "ineligible", ["unexpected_status"]
    return "eligible", []


def compare_snapshots(current: SourceSnapshot, current_text: str, baseline: SourceSnapshot | None,
                      baseline_text: str | None, run_id: UUID, decided_at: datetime) -> RunSourceComparison:
    """Hash shortcut plus context/version compatibility; never calls first fetch a product change."""
    if current.quality != "eligible":
        return RunSourceComparison(run_id=run_id, source_id=current.source_id, baseline_snapshot_id=None,
            current_snapshot_id=current.id, outcome="unavailable", quality="insufficient",
            reason_codes=[*current.quality_reason_codes, "current_ineligible"], decided_at=decided_at)
    if baseline is None or baseline_text is None:
        return RunSourceComparison(run_id=run_id, source_id=current.source_id, baseline_snapshot_id=None,
            current_snapshot_id=current.id, outcome="baseline_created", quality="complete",
            reason_codes=["first_eligible_capture"], decided_at=decided_at)
    if baseline.source_context_hash != current.source_context_hash:
        return RunSourceComparison(run_id=run_id, source_id=current.source_id,
            baseline_snapshot_id=baseline.id, current_snapshot_id=current.id, outcome="rebaseline_required",
            quality="insufficient", reason_codes=["comparison_context_changed"], decided_at=decided_at)
    if baseline.source_config_version != current.source_config_version:
        return RunSourceComparison(run_id=run_id, source_id=current.source_id,
            baseline_snapshot_id=baseline.id, current_snapshot_id=current.id, outcome="rebaseline_required",
            quality="insufficient", reason_codes=["source_config_changed"], decided_at=decided_at)
    if baseline.normalization_version != current.normalization_version:
        return RunSourceComparison(run_id=run_id, source_id=current.source_id,
            baseline_snapshot_id=baseline.id, current_snapshot_id=current.id, outcome="rebaseline_required",
            quality="insufficient", reason_codes=["normalization_changed"], decided_at=decided_at)
    if baseline.normalized_hash == current.normalized_hash:
        return RunSourceComparison(run_id=run_id, source_id=current.source_id,
            baseline_snapshot_id=baseline.id, current_snapshot_id=current.id, outcome="no_change",
            quality="complete", reason_codes=["identical_normalized_hash"], decided_at=decided_at)
    outcome: ComparisonOutcome = "changed"
    return RunSourceComparison(run_id=run_id, source_id=current.source_id, baseline_snapshot_id=baseline.id,
        current_snapshot_id=current.id, outcome=outcome, quality="complete",
        reason_codes=["normalized_hash_differs"], decided_at=decided_at)


def _excerpt(text: str) -> str:
    return text[:MAX_EXCERPT_CHARS]


def detect_candidates(before_text: str, after_text: str, run_id: UUID, source_id: UUID, before_id: UUID,
                      after_id: UUID, detected_at: datetime) -> list[ChangeCandidate]:
    """Position-independent section matching: moved sections are not changes, only add/remove/modify."""
    if before_text == after_text:
        return []
    before, after = split_sections(before_text), split_sections(after_text)

    def group(sections: list[Section]) -> dict[str | None, list[Section]]:
        grouped: dict[str | None, list[Section]] = {}
        for section in sections:
            grouped.setdefault(section.key, []).append(section)
        return grouped

    old, new = group(before), group(after)
    candidates: list[ChangeCandidate] = []
    seen: set[str] = set()
    for key in [*old.keys(), *[k for k in new.keys() if k not in old]]:
        olds, news = old.get(key, []), new.get(key, [])
        pairs = min(len(olds), len(news))
        for index in range(pairs):
            if olds[index].text != news[index].text:
                before_excerpt, after_excerpt = _excerpt(olds[index].text), _excerpt(news[index].text)
                fingerprint = hash_text(f"modified|{key}|{before_excerpt}|{after_excerpt}")
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    candidates.append(ChangeCandidate(id=uuid4(), run_id=run_id, source_id=source_id,
                        before_snapshot_id=before_id, after_snapshot_id=after_id, kind="modified", section=key,
                        before_start=olds[index].start, before_end=olds[index].end,
                        after_start=news[index].start, after_end=news[index].end,
                        before_excerpt=before_excerpt, after_excerpt=after_excerpt,
                        diff_algorithm_version=DIFF_ALGORITHM_VERSION, diff_hash=fingerprint,
                        detected_at=detected_at))
        for leftover in olds[pairs:]:
            before_excerpt = _excerpt(leftover.text)
            fingerprint = hash_text(f"removed|{key}|{before_excerpt}")
            if fingerprint not in seen:
                seen.add(fingerprint)
                candidates.append(ChangeCandidate(id=uuid4(), run_id=run_id, source_id=source_id,
                    before_snapshot_id=before_id, after_snapshot_id=after_id, kind="removed", section=key,
                    before_start=leftover.start, before_end=leftover.end, after_start=None, after_end=None,
                    before_excerpt=before_excerpt, after_excerpt="",
                    diff_algorithm_version=DIFF_ALGORITHM_VERSION, diff_hash=fingerprint, detected_at=detected_at))
        for leftover in news[pairs:]:
            after_excerpt = _excerpt(leftover.text)
            fingerprint = hash_text(f"added|{key}|{after_excerpt}")
            if fingerprint not in seen:
                seen.add(fingerprint)
                candidates.append(ChangeCandidate(id=uuid4(), run_id=run_id, source_id=source_id,
                    before_snapshot_id=before_id, after_snapshot_id=after_id, kind="added", section=key,
                    before_start=None, before_end=None, after_start=leftover.start, after_end=leftover.end,
                    before_excerpt="", after_excerpt=after_excerpt,
                    diff_algorithm_version=DIFF_ALGORITHM_VERSION, diff_hash=fingerprint, detected_at=detected_at))
    return candidates
