"""Static-first crawl orchestration with bounded browser fallback."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urljoin

import requests

from app.execution.tools.cache import get_cached, set_cached
from app.execution.tools.contracts import SourceMetadata, failure_result, success_result
from app.execution.tools.crawler.browser_fetcher import BrowserFetchError, render_page
from app.execution.tools.crawler.quality import (
    MIN_QUALITY_SCORE,
    calculate_quality_score,
    detect_access_challenge,
    looks_client_rendered,
)
from app.execution.tools.network_policy import (
    MAX_REDIRECTS,
    MAX_TRANSIENT_ATTEMPTS,
    transient_backoff,
    validate_external_url,
)


MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = (5, 15)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RENDERABLE_RESTRICTION_STATUSES = {401, 403, 429}


class StaticFetchError(RuntimeError):
    """A normalized failure while retrieving the original HTTP response."""

    def __init__(
        self,
        status: str,
        code: str,
        message: str,
        *,
        final_url: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.final_url = final_url
        self.retryable = retryable


class StaticCandidateError(RuntimeError):
    """A normalized failure while interpreting a static response."""

    def __init__(self, status: str, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


def _fetch_static_page(url: str, headers: dict[str, str]) -> tuple[Any, str]:
    current_url = url
    for redirect_index in range(MAX_REDIRECTS + 1):
        response = None
        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            try:
                response = requests.get(
                    current_url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
                break
            except requests.Timeout as exc:
                if attempt == MAX_TRANSIENT_ATTEMPTS - 1:
                    raise StaticFetchError(
                        "timeout",
                        "fetch_timeout",
                        f"Timed out while fetching '{url}'.",
                        final_url=current_url,
                        retryable=True,
                    ) from exc
                transient_backoff(attempt)
            except requests.ConnectionError as exc:
                if attempt == MAX_TRANSIENT_ATTEMPTS - 1:
                    raise StaticFetchError(
                        "http_error",
                        "fetch_failed",
                        f"Could not connect while fetching '{url}': {exc}",
                        final_url=current_url,
                        retryable=True,
                    ) from exc
                transient_backoff(attempt)
            except requests.RequestException as exc:
                raise StaticFetchError(
                    "http_error",
                    "fetch_failed",
                    str(exc),
                    final_url=current_url,
                    retryable=True,
                ) from exc

        if response is None:
            raise StaticFetchError(
                "internal_error",
                "missing_crawler_response",
                "Crawler returned no HTTP response.",
                final_url=current_url,
            )

        if getattr(response, "status_code", None) not in REDIRECT_STATUSES:
            return response, current_url

        response_headers = getattr(response, "headers", {})
        if not isinstance(response_headers, Mapping):
            response_headers = {}
        location = response_headers.get("location")
        if not location:
            return response, current_url

        next_url, redirect_error = validate_external_url(urljoin(current_url, location))
        if redirect_error or not next_url:
            raise StaticFetchError(
                "blocked",
                "redirect_target_blocked",
                redirect_error or "Redirect target is not allowed.",
                final_url=current_url,
            )
        current_url = next_url

    raise StaticFetchError(
        "http_error",
        "too_many_redirects",
        f"Request exceeded the {MAX_REDIRECTS} redirect limit.",
        final_url=current_url,
    )


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        return ""
    return str(headers.get("content-type", "")).split(";", 1)[0].strip().lower()


def _source_metadata(
    requested_url: str,
    final_url: str,
    status_code: Any,
    content_type: str,
) -> SourceMetadata:
    return SourceMetadata(
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code if isinstance(status_code, int) else None,
        content_type=content_type or None,
    )


def _make_candidate(
    *,
    html: str,
    requested_url: str,
    final_url: str,
    status_code: int | None,
    content_type: str,
    engine: Literal["static", "browser"],
    duration_ms: int,
    extract_page: Callable[[str, str], tuple[str, dict[str, Any], list[str]]],
    extra_warnings: tuple[str, ...] = (),
) -> dict[str, Any]:
    try:
        page_status, data, extraction_warnings = extract_page(final_url or requested_url, html)
    except Exception as exc:
        raise StaticCandidateError("parse_error", "html_parse_failed", str(exc)) from exc

    challenge_detected = detect_access_challenge(html) or status_code in RENDERABLE_RESTRICTION_STATUSES
    warnings = list(dict.fromkeys([*extraction_warnings, *extra_warnings]))
    if challenge_detected:
        warnings.append("The source appears to have returned an access challenge or restricted response.")
    if status_code is not None and status_code >= 400 and not challenge_detected:
        warnings.append(f"The rendered page returned HTTP status {status_code}.")

    quality_score = calculate_quality_score(data)
    if quality_score < MIN_QUALITY_SCORE:
        warnings.append("Extracted content is below the crawler's quality threshold.")
    return {
        "page_status": page_status,
        "data": data,
        "warnings": list(dict.fromkeys(warnings)),
        "quality_score": quality_score,
        "challenge_detected": challenge_detected,
        "http_error": status_code is not None and status_code >= 400,
        "engine": engine,
        "requested_url": requested_url,
        "final_url": final_url or requested_url,
        "status_code": status_code,
        "content_type": content_type or "text/html",
        "duration_ms": duration_ms,
    }


def _attempt_record(
    engine: str,
    status: str,
    *,
    duration_ms: int,
    quality_score: float | None = None,
    content_length: int | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "engine": engine,
        "status": status,
        "duration_ms": duration_ms,
    }
    if quality_score is not None:
        record["quality_score"] = quality_score
    if content_length is not None:
        record["content_length"] = content_length
    if error_code is not None:
        record["error_code"] = error_code
    return record


def _failure_json(
    *,
    status: str,
    code: str,
    message: str,
    requested_url: str,
    final_url: str,
    status_code: Any = None,
    content_type: str = "",
    duration_ms: int,
    attempts: list[dict[str, Any]],
    warnings: list[str] | None = None,
    retryable: bool = False,
    data: Any = None,
) -> str:
    result = failure_result(
        status,  # type: ignore[arg-type]
        code=code,
        message=message,
        retryable=retryable,
        tool_name="news_crawler",
        source=_source_metadata(requested_url, final_url, status_code, content_type),
        metadata={
            "duration_ms": duration_ms,
            "engine": attempts[-1]["engine"] if attempts else None,
            "rendered": any(
                attempt.get("engine") == "browser"
                and attempt.get("status") in {"success", "challenge", "low_quality", "parse_error"}
                for attempt in attempts
            ),
            "attempts": attempts,
            "warnings": list(warnings or []),
        },
    )
    if data is not None:
        result = result.model_copy(update={"data": data})
    return result.to_json()


def _candidate_result(
    candidate: dict[str, Any],
    *,
    requested_url: str,
    attempts: list[dict[str, Any]],
    duration_ms: int,
    mode: str,
) -> tuple[str, bool]:
    data = candidate["data"]
    content = str(data.get("text") or "")
    item_count = len(data.get("items") or [])
    quality_score = float(candidate["quality_score"])
    challenge_detected = bool(candidate["challenge_detected"])
    warnings = list(candidate["warnings"])
    metadata = {
        "duration_ms": duration_ms,
        "extractor": f"agentflow_{candidate['engine']}_html_v1",
        "engine": candidate["engine"],
        "rendered": candidate["engine"] == "browser",
        "requested_mode": mode,
        "quality_score": quality_score,
        "challenge_detected": challenge_detected,
        "paragraph_count": len(data.get("paragraphs") or []),
        "item_count": item_count,
        "content_length": len(content),
        "attempts": attempts,
        "warnings": warnings,
    }
    source = _source_metadata(
        requested_url,
        candidate["final_url"],
        candidate["status_code"],
        candidate["content_type"],
    )

    if challenge_detected and len(content) < 80 and item_count == 0:
        result = failure_result(
            "blocked",
            code="access_challenge_detected",
            message="The website returned an access challenge; no usable article content was extracted.",
            tool_name="news_crawler",
            source=source,
            metadata=metadata,
        ).model_copy(update={"data": data})
        return result.to_json(), False

    if candidate["page_status"] == "empty":
        result = failure_result(
            "empty",
            code="no_extractable_content",
            message="The page did not contain enough extractable content after the available fetch attempts.",
            tool_name="news_crawler",
            source=source,
            metadata=metadata,
        ).model_copy(update={"data": data})
        return result.to_json(), False

    is_partial = (
        challenge_detected
        or candidate["http_error"]
        or quality_score < MIN_QUALITY_SCORE
    )
    tool_result = success_result(
        data,
        tool_name="news_crawler",
        source=source,
        metadata=metadata,
        status="partial" if is_partial else "success",
    )
    has_usable_content = len(content) >= 80 or item_count > 0
    return (
        tool_result.to_json(),
        has_usable_content and not challenge_detected and not candidate["http_error"],
    )


def crawl_url(
    raw_url: str,
    mode: Literal["auto", "static", "browser"],
    *,
    extract_page: Callable[[str, str], tuple[str, dict[str, Any], list[str]]],
) -> str:
    """Fetch and extract a URL, rendering it only when auto mode needs fallback."""

    started = perf_counter()
    normalized_url = str(raw_url or "").strip()
    cached_key = f"news_crawler:{mode}:{normalized_url}"
    cached_result = get_cached(cached_key)
    if isinstance(cached_result, str):
        return cached_result

    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    static_error: StaticFetchError | StaticCandidateError | None = None
    final_url = normalized_url
    status_code: Any = None
    content_type = ""
    fallback_required = mode == "browser"
    browser_warnings: list[str] = []

    if mode != "browser":
        static_started = perf_counter()
        headers = {
            "User-Agent": "Mozilla/5.0 AgentFlowResearchCrawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            response, final_url = _fetch_static_page(normalized_url, headers)
        except StaticFetchError as exc:
            static_error = exc
            final_url = exc.final_url
            static_duration = round((perf_counter() - static_started) * 1000)
            attempts.append(
                _attempt_record("static", "failed", duration_ms=static_duration, error_code=exc.code)
            )
            fallback_required = mode == "auto" and exc.retryable
            if not fallback_required:
                return _failure_json(
                    status=exc.status,
                    code=exc.code,
                    message=str(exc),
                    requested_url=normalized_url,
                    final_url=final_url,
                    duration_ms=round((perf_counter() - started) * 1000),
                    attempts=attempts,
                    retryable=exc.retryable,
                )
        else:
            status_code = getattr(response, "status_code", None)
            response_url = getattr(response, "url", None)
            if isinstance(response_url, str) and response_url:
                final_url = response_url
            content_type = _response_content_type(response)
            raw_content = getattr(response, "content", None)
            static_duration = round((perf_counter() - static_started) * 1000)

            if isinstance(raw_content, bytes) and len(raw_content) > MAX_RESPONSE_BYTES:
                return _failure_json(
                    status="blocked",
                    code="response_too_large",
                    message=f"Response exceeds the {MAX_RESPONSE_BYTES} byte limit.",
                    requested_url=normalized_url,
                    final_url=final_url,
                    status_code=status_code,
                    content_type=content_type,
                    duration_ms=round((perf_counter() - started) * 1000),
                    attempts=[_attempt_record("static", "rejected", duration_ms=static_duration)],
                )

            status_is_success = isinstance(status_code, int) and 200 <= status_code < 300
            status_can_render = status_code in RENDERABLE_RESTRICTION_STATUSES
            if not status_is_success and not status_can_render:
                retryable = status_code in {408, 425} or (
                    isinstance(status_code, int) and status_code >= 500
                )
                return _failure_json(
                    status="http_error",
                    code="unexpected_http_status",
                    message=f"Failed to fetch URL (HTTP status {status_code}).",
                    requested_url=normalized_url,
                    final_url=final_url,
                    status_code=status_code,
                    content_type=content_type,
                    duration_ms=round((perf_counter() - started) * 1000),
                    attempts=[_attempt_record("static", "http_error", duration_ms=static_duration)],
                    retryable=retryable,
                )

            if content_type and "html" not in content_type and "xhtml" not in content_type:
                if mode != "auto" or not status_can_render:
                    return _failure_json(
                        status="blocked",
                        code="unsupported_content_type",
                        message=f"Expected HTML but received '{content_type}'.",
                        requested_url=normalized_url,
                        final_url=final_url,
                        status_code=status_code,
                        content_type=content_type,
                        duration_ms=round((perf_counter() - started) * 1000),
                        attempts=[_attempt_record("static", "unsupported_content", duration_ms=static_duration)],
                    )
                fallback_required = True
            else:
                html = getattr(response, "text", "")
                try:
                    candidate = _make_candidate(
                        html=html,
                        requested_url=normalized_url,
                        final_url=final_url,
                        status_code=status_code if isinstance(status_code, int) else None,
                        content_type=content_type,
                        engine="static",
                        duration_ms=static_duration,
                        extract_page=extract_page,
                    )
                    candidates.append(candidate)
                    extracted_length = len(str(candidate["data"].get("text") or ""))
                    fallback_required = (
                        mode == "auto"
                        and (
                            status_can_render
                            or candidate["challenge_detected"]
                            or (
                                candidate["quality_score"] < MIN_QUALITY_SCORE
                                and extracted_length < 400
                                and (
                                    looks_client_rendered(html)
                                    or (extracted_length < 80 and "<script" in html.lower())
                                )
                            )
                        )
                    )
                    if (
                        mode == "auto"
                        and not fallback_required
                        and candidate["quality_score"] < MIN_QUALITY_SCORE
                    ):
                        candidate["warnings"].append(
                            "No common JavaScript app-shell signal was found, so the static result was retained."
                        )
                    attempt_status = (
                        "challenge"
                        if candidate["challenge_detected"]
                        else "low_quality"
                        if candidate["quality_score"] < MIN_QUALITY_SCORE
                        else "success"
                    )
                    attempts.append(
                        _attempt_record(
                            "static",
                            attempt_status,
                            duration_ms=static_duration,
                            quality_score=candidate["quality_score"],
                            content_length=len(str(candidate["data"].get("text") or "")),
                        )
                    )
                except StaticCandidateError as exc:
                    static_error = exc
                    fallback_required = mode == "auto"
                    attempts.append(
                        _attempt_record(
                            "static", "parse_error", duration_ms=static_duration, error_code=exc.code
                        )
                    )

            if status_can_render:
                fallback_required = mode == "auto"
                if mode == "static":
                    return _failure_json(
                        status="http_error",
                        code="restricted_http_status",
                        message=f"The website rejected the static request (HTTP status {status_code}).",
                        requested_url=normalized_url,
                        final_url=final_url,
                        status_code=status_code,
                        content_type=content_type,
                        duration_ms=round((perf_counter() - started) * 1000),
                        attempts=attempts,
                        retryable=status_code == 429,
                    )

    if mode == "static" or not fallback_required:
        if candidates:
            selected = candidates[0]
            result_json, cacheable = _candidate_result(
                selected,
                requested_url=normalized_url,
                attempts=attempts,
                duration_ms=round((perf_counter() - started) * 1000),
                mode=mode,
            )
            if cacheable:
                set_cached(cached_key, result_json)
            return result_json
        if static_error:
            return _failure_json(
                status=static_error.status,
                code=static_error.code,
                message=str(static_error),
                requested_url=normalized_url,
                final_url=static_error.final_url,
                duration_ms=round((perf_counter() - started) * 1000),
                attempts=attempts,
                retryable=static_error.retryable,
            )

    browser_started = perf_counter()
    try:
        rendered = render_page(normalized_url)
    except BrowserFetchError as exc:
        browser_duration = round((perf_counter() - browser_started) * 1000)
        attempts.append(
            _attempt_record("browser", "unavailable", duration_ms=browser_duration, error_code=exc.code)
        )
        browser_warnings.append(str(exc))
        if candidates:
            selected = candidates[0]
            selected["warnings"] = list(dict.fromkeys([*selected["warnings"], *browser_warnings]))
            result_json, cacheable = _candidate_result(
                selected,
                requested_url=normalized_url,
                attempts=attempts,
                duration_ms=round((perf_counter() - started) * 1000),
                mode=mode,
            )
            if cacheable:
                set_cached(cached_key, result_json)
            return result_json

        if static_error:
            status = "timeout" if static_error.status == "timeout" else static_error.status
            return _failure_json(
                status=status,
                code=static_error.code,
                message=f"{static_error}. Browser fallback failed: {exc}",
                requested_url=normalized_url,
                final_url=static_error.final_url,
                duration_ms=round((perf_counter() - started) * 1000),
                attempts=attempts,
                warnings=browser_warnings,
                retryable=static_error.retryable or exc.retryable,
            )
        if exc.code == "browser_capacity_reached":
            browser_failure_status = "timeout"
        elif exc.code in {
            "outbound_url_blocked",
            "browser_final_url_blocked",
            "browser_outbound_request_blocked",
        }:
            browser_failure_status = "blocked"
        else:
            browser_failure_status = "internal_error"
        return _failure_json(
            status=browser_failure_status,
            code=exc.code,
            message=str(exc),
            requested_url=normalized_url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            duration_ms=round((perf_counter() - started) * 1000),
            attempts=attempts,
            warnings=browser_warnings,
            retryable=exc.retryable,
        )

    browser_status = rendered.status_code
    browser_content_type = "text/html"
    try:
        browser_candidate = _make_candidate(
            html=rendered.html,
            requested_url=normalized_url,
            final_url=rendered.final_url,
            status_code=browser_status,
            content_type=browser_content_type,
            engine="browser",
            duration_ms=rendered.duration_ms,
            extract_page=extract_page,
            extra_warnings=rendered.warnings,
        )
    except StaticCandidateError as exc:
        attempts.append(
            _attempt_record("browser", "parse_error", duration_ms=rendered.duration_ms, error_code=exc.code)
        )
        return _failure_json(
            status="parse_error",
            code=exc.code,
            message=str(exc),
            requested_url=normalized_url,
            final_url=rendered.final_url,
            status_code=browser_status,
            content_type=browser_content_type,
            duration_ms=round((perf_counter() - started) * 1000),
            attempts=attempts,
        )

    attempts.append(
        _attempt_record(
            "browser",
            "challenge"
            if browser_candidate["challenge_detected"]
            else "low_quality"
            if browser_candidate["quality_score"] < MIN_QUALITY_SCORE
            else "success",
            duration_ms=rendered.duration_ms,
            quality_score=browser_candidate["quality_score"],
            content_length=len(str(browser_candidate["data"].get("text") or "")),
        )
    )
    candidates.append(browser_candidate)
    selected = max(
        candidates,
        key=lambda item: (
            not item["challenge_detected"],
            item["quality_score"],
            item["engine"] == "browser",
        ),
    )
    result_json, cacheable = _candidate_result(
        selected,
        requested_url=normalized_url,
        attempts=attempts,
        duration_ms=round((perf_counter() - started) * 1000),
        mode=mode,
    )
    if cacheable:
        set_cached(cached_key, result_json)
    return result_json
