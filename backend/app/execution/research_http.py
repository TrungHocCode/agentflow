"""Normalize retrieved HTTP bodies into the existing research source contract."""

import json
import re

from app.execution.tools.contracts import ToolResult, failure_result
from app.execution.tools.crawler.quality import detect_access_challenge
from app.execution.tools.http_request_tool import MAX_RESPONSE_BYTES
from app.execution.tools.news_crawler_tool import HTMLContentExtractor, MIN_ARTICLE_TEXT_LENGTH


def normalize_http_source(result: ToolResult, method: str = "GET") -> ToolResult:
    """Extract a textual source without refetching, rendering, or inventing provenance."""
    if not result.ok:
        return result

    def reject(code: str, message: str) -> ToolResult:
        return failure_result("parse_error", code=code, message=message,
                              tool_name="http_request", source=result.source)

    if method.strip().upper() != "GET":
        return reject("http_evidence_method_unsupported", "Only GET response bodies are research sources.")
    if result.source is None or not (result.source.final_url or result.source.requested_url):
        return reject("http_evidence_source_missing", "HTTP response has no source URL.")
    if result.source.status_code is not None and not 200 <= result.source.status_code < 300:
        return reject("http_evidence_status_invalid", "HTTP response status is not successful.")
    body = result.data.get("body") if isinstance(result.data, dict) else None
    content_type = (result.source.content_type or "").split(";", 1)[0].strip().lower()
    html = content_type in {"text/html", "application/xhtml+xml"}
    structured = content_type == "application/json" or content_type.endswith("+json")
    warnings = list(result.metadata.warnings)
    if not content_type:
        # Older tool responses may omit MIME; infer only supported string/JSON shapes.
        structured = isinstance(body, (dict, list))
        html = isinstance(body, str) and bool(re.match(r"\s*(?:<!doctype html|<html\b)", body, re.I))
        warnings.append("HTTP content type was absent; source format was inferred from its body.")
    elif not (html or structured or content_type.startswith("text/")):
        return reject("http_evidence_type_unsupported", f"Unsupported research response type: {content_type}.")
    if structured:
        if not isinstance(body, (dict, list, str, int, float, bool)):
            return reject("http_evidence_body_empty", "HTTP JSON response has no usable body.")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                return reject("http_evidence_json_invalid", "HTTP response is not valid structured JSON.")
        text = json.dumps(body, ensure_ascii=False, indent=2)
        warnings.append("Evidence offsets refer to the normalized JSON response, not original HTTP bytes.")
    elif isinstance(body, str):
        text = body
    else:
        return reject("http_evidence_body_unsupported", "HTTP response body is not readable text.")
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return reject("http_evidence_body_too_large", "HTTP source exceeds the bounded response size.")
    if html:
        if detect_access_challenge(text):
            return reject("http_evidence_access_challenge", "HTTP response contains an access challenge.")
        parser = HTMLContentExtractor()
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            return reject("http_evidence_html_invalid", "HTTP HTML response could not be parsed.")
        semantic_size = sum(len(block["text"]) for block in parser.semantic_blocks)
        total_size = sum(len(block["text"]) for block in parser.blocks)
        selected = parser.semantic_blocks if (
            semantic_size >= MIN_ARTICLE_TEXT_LENGTH and semantic_size >= total_size * 0.35
        ) else parser.blocks
        # Do not apply the crawler presentation's fixed block limit to stored evidence.
        text = "\n\n".join(dict.fromkeys(block["text"] for block in selected))
        if not text.strip():
            text = re.sub(r"\s+", " ", " ".join(parser.fallback_text)).strip()
    if not text.strip():
        return reject("http_evidence_body_empty", "HTTP response has no extractable source text.")
    return result.model_copy(update={
        "data": {"text": text, "text_truncated": False},
        "metadata": result.metadata.model_copy(update={"warnings": warnings}),
    })
