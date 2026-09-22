"""Bounded, SSRF-aware HTTP request tool."""

from __future__ import annotations

import json
from collections.abc import Mapping
from time import perf_counter
from typing import Any
from urllib.parse import urljoin

import requests
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import SourceMetadata, failure_result, success_result
from app.execution.tools.network_policy import MAX_TRANSIENT_ATTEMPTS, transient_backoff, validate_external_url


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = (5, 15)
MAX_REDIRECTS = 3
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}


class HttpRequestInput(BaseModel):
    url: str = Field(description="External HTTP(S) URL.")
    method: str = Field(default="GET", description="GET, POST, PUT, PATCH, DELETE or HEAD.")
    headers: dict[str, str] | None = None
    data: str | None = None


def _response_body(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return getattr(response, "text", "")


@ToolRegistry.register_tool(name="http_request")
@tool("http_request", args_schema=HttpRequestInput)
def http_request(url: str, method: str = "GET", headers: dict[str, str] | None = None, data: str | None = None) -> str:
    """Send one bounded external HTTP request with redirect validation."""

    started = perf_counter()
    method = method.upper().strip()
    if method not in ALLOWED_METHODS:
        return failure_result(
            "invalid_input",
            code="unsupported_http_method",
            message=f"HTTP method '{method}' is not allowed.",
            tool_name="http_request",
        ).to_json()

    normalized_url, validation_error = validate_external_url(url)
    if validation_error:
        return failure_result(
            "blocked",
            code="outbound_url_blocked",
            message=validation_error,
            tool_name="http_request",
        ).to_json()

    payload: Any = None
    if data:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = data

    current_url = normalized_url
    response = None
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            attempts = MAX_TRANSIENT_ATTEMPTS if method in {"GET", "HEAD", "PUT", "DELETE"} else 1
            for attempt in range(attempts):
                try:
                    response = requests.request(
                        method=method,
                        url=current_url,
                        headers=headers,
                        json=payload if isinstance(payload, (dict, list)) else None,
                        data=payload if isinstance(payload, str) else None,
                        timeout=REQUEST_TIMEOUT,
                        allow_redirects=False,
                    )
                    break
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == attempts - 1:
                        raise
                    transient_backoff(attempt)
            status_code = getattr(response, "status_code", None)
            if status_code not in {301, 302, 303, 307, 308}:
                break
            response_headers = getattr(response, "headers", {})
            location = response_headers.get("location") if isinstance(response_headers, Mapping) else None
            if not location:
                break
            next_url, redirect_error = validate_external_url(urljoin(current_url, location))
            if redirect_error:
                return failure_result(
                    "blocked",
                    code="redirect_target_blocked",
                    message=redirect_error,
                    tool_name="http_request",
                    source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
                    metadata={"duration_ms": round((perf_counter() - started) * 1000)},
                ).to_json()
            current_url = next_url
        else:
            return failure_result(
                "http_error",
                code="too_many_redirects",
                message=f"Request exceeded the {MAX_REDIRECTS} redirect limit.",
                retryable=False,
                tool_name="http_request",
                source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
                metadata={"duration_ms": round((perf_counter() - started) * 1000)},
            ).to_json()
    except requests.Timeout:
        return failure_result(
            "timeout",
            code="request_timeout",
            message="HTTP request timed out.",
            retryable=True,
            tool_name="http_request",
            source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()
    except requests.RequestException as exc:
        return failure_result(
            "http_error",
            code="request_failed",
            message=str(exc),
            retryable=True,
            tool_name="http_request",
            source=SourceMetadata(requested_url=normalized_url, final_url=current_url),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    if response is None:
        return failure_result(
            "internal_error",
            code="missing_http_response",
            message="HTTP client returned no response.",
            tool_name="http_request",
        ).to_json()

    status_code = getattr(response, "status_code", None)
    response_headers = getattr(response, "headers", {})
    content_type = ""
    if isinstance(response_headers, Mapping):
        content_type = str(response_headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    raw_content = getattr(response, "content", None)
    if isinstance(raw_content, bytes) and len(raw_content) > MAX_RESPONSE_BYTES:
        return failure_result(
            "blocked",
            code="response_too_large",
            message=f"Response exceeds the {MAX_RESPONSE_BYTES} byte limit.",
            tool_name="http_request",
            source=SourceMetadata(requested_url=normalized_url, final_url=current_url, status_code=status_code, content_type=content_type),
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).to_json()

    source = SourceMetadata(
        requested_url=normalized_url,
        final_url=current_url,
        status_code=status_code if isinstance(status_code, int) else None,
        content_type=content_type or None,
    )
    body = _response_body(response)
    if not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
        return failure_result(
            "http_error",
            code="unexpected_http_status",
            message=f"HTTP request returned status {status_code}.",
            retryable=status_code in {408, 425, 429} or (isinstance(status_code, int) and status_code >= 500),
            tool_name="http_request",
            source=source,
            metadata={"duration_ms": round((perf_counter() - started) * 1000)},
        ).model_copy(update={"data": {"body": body}}).to_json()

    return success_result(
        {
            "status_code": status_code,
            "body": body,
            "legacy_message": f"HTTP Status: {status_code}\nResponse Body:\n{body}",
        },
        tool_name="http_request",
        source=source,
        metadata={"duration_ms": round((perf_counter() - started) * 1000)},
    ).to_json()
