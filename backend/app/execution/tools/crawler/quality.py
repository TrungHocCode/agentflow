"""Lightweight quality and access-challenge checks for extracted pages."""

from __future__ import annotations

from typing import Any


CHALLENGE_MARKERS = (
    "just a moment...",
    "verify you are human",
    "checking your browser before accessing",
    "checking if the site connection is secure",
    "enable javascript and cookies to continue",
    "please complete the security check",
    "unusual traffic from your computer network",
    "you have been blocked",
    "attention required! | cloudflare",
    "cf-challenge",
    "challenge-platform",
    "captcha-delivery",
    "access denied |",
)
CLIENT_RENDER_MARKERS = (
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    "__next",
    "__nuxt",
    "data-reactroot",
    "ng-version",
    "data-server-rendered",
)

MIN_QUALITY_SCORE = 0.24


def detect_access_challenge(html: str) -> bool:
    """Detect common access-denial and bot-challenge pages without bypassing them."""

    sample = (html or "")[:250_000].lower()
    return any(marker in sample for marker in CHALLENGE_MARKERS)


def looks_client_rendered(html: str) -> bool:
    """Identify common JavaScript app shells before spending time on Chromium."""

    sample = (html or "")[:250_000].lower()
    return "<script" in sample and (
        any(marker in sample for marker in CLIENT_RENDER_MARKERS)
        or "application/json" in sample
        or "type=module" in sample
    )


def calculate_quality_score(data: dict[str, Any]) -> float:
    """Return a bounded heuristic score based on useful extracted content."""

    content_type = data.get("content_type")
    text = str(data.get("text") or "")
    title = str(data.get("title") or "").strip()
    paragraphs = data.get("paragraphs") or []
    items = data.get("items") or []

    if content_type == "listing":
        score = min(len(items) / 8, 1.0) * 0.78
        score += min(len(text) / 1200, 1.0) * 0.12
        score += 0.10 if title else 0.0
        return round(min(score, 1.0), 3)

    score = min(len(text) / 1200, 1.0) * 0.55
    score += min(len(paragraphs) / 6, 1.0) * 0.22
    score += 0.10 if title else 0.0
    metadata = data.get("metadata") or {}
    score += 0.08 if metadata.get("description") else 0.0
    score += 0.05 if metadata.get("extraction_region") in {"main", "article"} else 0.0
    return round(min(score, 1.0), 3)
