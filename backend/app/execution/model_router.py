"""Purpose-based model routing for local inference."""

import re
from enum import Enum

from app.core.config import settings


class InferencePurpose(str, Enum):
    """The kind of inference being performed by AgentFlow."""

    CHAT = "chat"
    PLANNER = "planner"
    WORKER = "worker"


_WORKFLOW_CUES = (
    "research",
    "tìm kiếm",
    "tìm thông tin",
    "tìm hiểu về",
    "nghiên cứu về",
    "nghiên cứu thông tin",
    "hãy nghiên cứu",
    "thu thập thông tin",
    "find sources",
    "search for",
    "gather information",
    "collect information",
    "crawl",
    "scrape",
    "cào ",
    "benchmark",
    "so sánh",
    "compare ",
    "tổng hợp",
    "viết báo cáo",
    "tạo báo cáo",
    "generate a report",
    "write a report",
    "workflow",
    "quy trình",
    "lập kế hoạch",
    "chạy tự động",
)

_DIRECT_CHAT_CUES = (
    "là gì",
    "nghĩa là gì",
    "tại sao",
    "vì sao",
    "như thế nào",
    "giải thích",
    "bạn nghĩ sao",
    "nghĩ sao",
    "what is ",
    "what does ",
    "why ",
    "how do ",
    "how does ",
    "explain ",
    "what do you think",
    "hello",
    "hi",
    "chào",
    "cảm ơn",
    "thank you",
    "thanks",
)

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def route_conversation(
    content: str,
    *,
    has_pending_plan: bool = False,
    previous_decision: str | None = None,
) -> InferencePurpose:
    """Route obvious conversational turns to the small model; default to the planner otherwise."""

    normalized = " ".join(content.casefold().split())
    if has_pending_plan or previous_decision == "clarify":
        return InferencePurpose.PLANNER

    if _URL_PATTERN.search(normalized) or any(cue in normalized for cue in _WORKFLOW_CUES):
        return InferencePurpose.PLANNER

    if previous_decision == "answer" and len(normalized) <= 500:
        return InferencePurpose.CHAT

    if len(normalized) <= 240 and any(cue in normalized for cue in _DIRECT_CHAT_CUES):
        return InferencePurpose.CHAT

    if "?" in normalized and len(normalized) <= 240:
        return InferencePurpose.CHAT

    # This product's primary job is building research workflows, so uncertainty
    # should favor the more capable planning model.
    return InferencePurpose.PLANNER


def model_name_for(purpose: InferencePurpose | str) -> str:
    """Return the configured local model for a purpose, defaulting safely to planning."""

    try:
        resolved_purpose = InferencePurpose(purpose)
    except (TypeError, ValueError):
        resolved_purpose = InferencePurpose.PLANNER

    return {
        InferencePurpose.CHAT: settings.LLM_CHAT_MODEL,
        InferencePurpose.PLANNER: settings.LLM_PLANNER_MODEL,
        InferencePurpose.WORKER: settings.LLM_WORKER_MODEL,
    }[resolved_purpose]
