"""Shared, privacy-conscious logging and correlation context."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator


CORRELATION_FIELDS = (
    "request_id",
    "conversation_id",
    "turn_id",
    "run_id",
    "task_execution_id",
    "llm_call_id",
    "tool_call_id",
    "command_id",
)
_context: ContextVar[dict[str, str]] = ContextVar("agentflow_log_context", default={})

_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_SECRET_RE = re.compile(
    r"(?i)([\"']?(?:access[_-]?token|refresh[_-]?token|token|authorization|password|secret|api[_-]?key)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}&]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|token|password|secret|api_key|key|signature)=)"
    r"[^&#\s]+"
)
_URL_CREDENTIALS_RE = re.compile(r"(?i)(://)[^/@\s]+:[^/@\s]+@")
_MAX_MESSAGE_CHARS = 4000
_MAX_EXCEPTION_CHARS = 12000
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def redact_sensitive(value: str) -> str:
    """Redact common credentials before diagnostic strings reach a log sink."""

    redacted = _BEARER_RE.sub(r"\1[REDACTED]", value)
    redacted = _SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    return _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", redacted)


@contextmanager
def bind_context(**fields: str | None) -> Iterator[None]:
    """Temporarily bind correlation identifiers to logs in this async context."""

    current = _context.get()
    updated = dict(current)
    for name, value in fields.items():
        if name in CORRELATION_FIELDS and value:
            updated[name] = str(value)[:128]
    token = _context.set(updated)
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, str]:
    """Return a copy of the active correlation identifiers."""

    return dict(_context.get())


class JsonLogFormatter(logging.Formatter):
    """Emit compact JSON records without serializing arbitrary ``extra`` values."""

    def format(self, record: logging.LogRecord) -> str:
        context = current_context()
        for field in CORRELATION_FIELDS:
            explicit = getattr(record, field, None)
            if explicit:
                context[field] = str(explicit)[:128]

        message = redact_sensitive(record.getMessage())[:_MAX_MESSAGE_CHARS]
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        payload.update(context)
        for field in (
            "error_id",
            "error_code",
            "error_type",
            "event_type",
            "tool_name",
            "tool_status",
            "model_name",
            "purpose",
            "iteration",
            "run_status",
            "http_method",
            "http_path",
            "status_code",
            "duration_ms",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = (
                    value
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                    else redact_sensitive(str(value))[:256]
                )

        if record.exc_info:
            exception = self.formatException(record.exc_info)
            payload["exception"] = redact_sensitive(exception)[:_MAX_EXCEPTION_CHARS]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure API and worker processes to emit structured logs to stderr."""

    resolved_level = logging.getLevelName(level.strip().upper())
    if not isinstance(resolved_level, int):
        raise ValueError(f"Unsupported LOG_LEVEL: {level!r}")

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        named_logger = logging.getLogger(logger_name)
        named_logger.handlers.clear()
        named_logger.propagate = True
        if logger_name == "uvicorn.access":
            named_logger.disabled = True


class RequestCorrelationMiddleware:
    """Attach a safe request identifier to logs and the HTTP response."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_request_id = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"x-request-id"
            ),
            "",
        )
        request_id = raw_request_id if _REQUEST_ID_RE.fullmatch(raw_request_id) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = time.perf_counter()
        response_status = 500

        async def send_with_request_id(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
                headers = list(message.get("headers", []))
                headers = [(key, value) for key, value in headers if key.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        with bind_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            finally:
                logger = logging.getLogger("agentflow.http")
                logger.info(
                    "HTTP request completed",
                    extra={
                        "event_type": "http_request",
                        "http_method": scope.get("method", ""),
                        "http_path": scope.get("path", ""),
                        "status_code": response_status,
                        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    },
                )
