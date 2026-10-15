"""CI-P9 structured history Q&A: code-owned intent routing over durable records.

Factual answers are assembled from retrieved excerpts with resolvable snapshot spans.
Unknown questions return not-observed; the model is never asked to recall facts.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.snapshot_service import SnapshotService
from app.modules.competitive_intelligence.timeline_contracts import (
    Citation, HistoryAnswer, QuestionIntent,
)
from app.modules.competitive_intelligence.timeline_service import TimelineService

TEMPORAL_WORDS = {"when", "since", "history", "timeline", "ever", "before", "after", "first", "last",
                  "changed", "change", "evolution"}
SUMMARY_WORDS = {"summarize", "summary", "recap", "overview", "digest", "everything", "all changes"}
STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "did", "does",
             "what", "when", "how", "is", "was", "were", "their", "our", "they", "them", "it"}
EXCERPT_CHARS = 2000


def classify_intent(question: str) -> QuestionIntent:
    """Route by keyword sets; temporal wins ties because it needs the timeline."""
    tokens = {word.strip("?,.!;:\"'").lower() for word in question.split()}
    if tokens & TEMPORAL_WORDS:
        return "temporal"
    if tokens & SUMMARY_WORDS:
        return "summary"
    return "factual" if keywords(question) else "keyword"


def keywords(question: str) -> set[str]:
    return {word for word in _tokens(question) if len(word) > 3} - STOPWORDS


def _tokens(text: str) -> set[str]:
    stripped = [word.strip("?,.!;:\"'()[]") for word in text.lower().replace("→", " ").split()]
    return {word for word in stripped if word}


class HistoryQA:
    def __init__(self, timeline: TimelineService, snapshots: SnapshotService,
                 intelligence: IntelligenceService) -> None:
        self.timeline, self.snapshots, self.intelligence = timeline, snapshots, intelligence

    async def ask(self, owner_id: str, watchlist_id: UUID, question: str) -> HistoryAnswer:
        """Answer from owned records only; cite spans or return not-observed."""
        intent = classify_intent(question)
        if intent == "temporal":
            return await self._answer_temporal(owner_id, watchlist_id, question)
        if intent == "summary":
            return await self._answer_summary(owner_id, watchlist_id, question)
        if intent == "factual":
            return await self._answer_factual(owner_id, watchlist_id, question)
        return await self._answer_keyword(owner_id, watchlist_id, question)

    async def _cite(self, owner_id: str, snapshot_id: UUID, limit: int = 500) -> Citation | None:
        try:
            content = await self.snapshots.read_content(str(snapshot_id), owner_id, "normalized", 0, limit)
        except Exception:
            return None
        return Citation(snapshot_id=snapshot_id, start_offset=content.start_offset,
                        end_offset=content.start_offset + len(content.text), excerpt=content.text)

    async def _answer_temporal(self, owner_id: str, watchlist_id: UUID, question: str) -> HistoryAnswer:
        events = await self.timeline.competitor_timeline(owner_id, watchlist_id, None, 50)
        wanted = keywords(question)
        matched = [event for event in events if wanted and wanted & _tokens(event.summary)]
        if not matched:
            return HistoryAnswer(question=question, intent="temporal", verdict="not_observed",
                                 text="No recorded change matches that description.")
        lines, citations = [], []
        for event in matched[:10]:
            lines.append(f"On {event.at.date().isoformat()}: {event.summary}")
            for snapshot_id in event.snapshot_ids[:2]:
                citation = await self._cite(owner_id, snapshot_id)
                if citation is not None:
                    citations.append(citation)
        return HistoryAnswer(question=question, intent="temporal", verdict="answered",
                             text="\n".join(lines)[:8000], citations=citations[:20])

    async def _answer_summary(self, owner_id: str, watchlist_id: UUID, question: str) -> HistoryAnswer:
        briefs, _ = await self.timeline.briefs.list_briefs(str(watchlist_id), owner_id, 5, 0)
        if not briefs:
            return HistoryAnswer(question=question, intent="summary", verdict="not_observed",
                                 text="No briefs recorded for this watchlist yet.")
        lines, citations = [], []
        for brief in briefs:
            lines.append(f"On {brief.created_at.date().isoformat()} ({brief.outcome}): {brief.summary}")
            for entry in brief.source_coverage:
                if entry.current_snapshot_id is not None:
                    citation = await self._cite(owner_id, entry.current_snapshot_id)
                    if citation is not None:
                        citations.append(citation)
        return HistoryAnswer(question=question, intent="summary", verdict="answered",
                             text="\n".join(lines)[:8000], citations=citations[:20])

    async def _answer_factual(self, owner_id: str, watchlist_id: UUID, question: str) -> HistoryAnswer:
        watchlist = await self.intelligence.get(str(watchlist_id), owner_id)
        wanted = keywords(question)
        examined: list[str] = []
        for product in watchlist.current_revision.config.products:
            for source in product.sources:
                if source.id is None:
                    continue
                baseline = await self.snapshots.latest_baseline(str(source.id), owner_id)
                if baseline is None:
                    examined.append(source.url)
                    continue
                content = await self.snapshots.read_content(str(baseline.id), owner_id, "normalized", 0,
                                                           EXCERPT_CHARS)
                lowered = content.text.lower()
                hits = [word for word in wanted if word in lowered]
                if hits:
                    end = min(len(content.text), EXCERPT_CHARS)
                    return HistoryAnswer(
                        question=question, intent="factual", verdict="answered",
                        text=(f"From {baseline.final_url} observed {baseline.observed_at.date().isoformat()}: "
                              f"{content.text[:1000]}")[:8000],
                        citations=[Citation(snapshot_id=baseline.id, start_offset=0, end_offset=end,
                                            excerpt=content.text[:1000])])
                examined.append(source.url)
        return HistoryAnswer(question=question, intent="factual", verdict="not_observed",
                             text=f"No observed baselines mention that. Checked: {', '.join(examined) or 'none'}.")

    async def _answer_keyword(self, owner_id: str, watchlist_id: UUID, question: str) -> HistoryAnswer:
        events = await self.timeline.competitor_timeline(owner_id, watchlist_id, None, 50)
        wanted = keywords(question)
        matched = [event for event in events if wanted and wanted & _tokens(event.summary)]
        if not matched:
            return await self._answer_factual(owner_id, watchlist_id, question)
        event = matched[0]
        citations = []
        for snapshot_id in event.snapshot_ids[:2]:
            citation = await self._cite(owner_id, snapshot_id)
            if citation is not None:
                citations.append(citation)
        if not citations:
            return HistoryAnswer(question=question, intent="keyword", verdict="not_observed",
                                 text="Matched a record without retrievable evidence.")
        return HistoryAnswer(question=question, intent="keyword", verdict="answered",
                             text=f"Closest record ({event.at.date().isoformat()}): {event.summary}"[:8000],
                             citations=citations)
