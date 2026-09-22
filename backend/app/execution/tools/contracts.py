"""Shared contracts for tool execution and observability.

Tools are exposed to LangChain as strings, but the execution core needs a
stable machine-readable result.  The helpers in this module bridge those two
worlds without coupling individual tools to the worker implementation.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ToolStatus = Literal[
    "success",
    "partial",
    "empty",
    "invalid_input",
    "http_error",
    "timeout",
    "blocked",
    "parse_error",
    "internal_error",
]


class SourceMetadata(BaseModel):
    """Provenance information for data fetched from an external source."""

    requested_url: str | None = None
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None


class ToolError(BaseModel):
    """A normalized tool failure that can be handled by a worker."""

    code: str
    message: str
    retryable: bool = False


class ToolMetadata(BaseModel):
    """Execution and quality metadata shared by all tool results."""

    model_config = ConfigDict(extra="allow")

    tool_name: str | None = None
    duration_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    """Canonical internal result for a tool invocation."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: ToolStatus
    data: Any = None
    source: SourceMetadata | None = None
    metadata: ToolMetadata = Field(default_factory=ToolMetadata)
    error: ToolError | None = None

    def to_json(self) -> str:
        """Serialize the result for LangChain's string-based tool channel."""

        return self.model_dump_json(exclude_none=True)


def success_result(
    data: Any,
    *,
    tool_name: str | None = None,
    source: SourceMetadata | None = None,
    metadata: dict[str, Any] | None = None,
    status: Literal["success", "partial"] = "success",
) -> ToolResult:
    """Build a successful or partially successful result."""

    metadata_values = dict(metadata or {})
    if tool_name is not None:
        metadata_values.setdefault("tool_name", tool_name)
    return ToolResult(
        ok=True,
        status=status,
        data=data,
        source=source,
        metadata=ToolMetadata(**metadata_values),
    )


def failure_result(
    status: Exclude[ToolStatus, "success", "partial"],
    *,
    code: str,
    message: str,
    retryable: bool = False,
    tool_name: str | None = None,
    source: SourceMetadata | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    """Build a normalized failure result."""

    metadata_values = dict(metadata or {})
    if tool_name is not None:
        metadata_values.setdefault("tool_name", tool_name)
    return ToolResult(
        ok=False,
        status=status,
        source=source,
        metadata=ToolMetadata(**metadata_values),
        error=ToolError(code=code, message=message, retryable=retryable),
    )


def parse_tool_result(value: Any, *, tool_name: str | None = None) -> ToolResult:
    """Parse a structured tool result or wrap a legacy string result.

    Existing tools in AgentFlow still return plain text.  Treating that output
    as a legacy success/error keeps the migration incremental while allowing
    the Tool Lab and workers to consume one normalized shape.
    """

    if isinstance(value, ToolResult):
        return value

    if isinstance(value, dict) and {"ok", "status"}.issubset(value):
        return ToolResult.model_validate(value)

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict) and {"ok", "status"}.issubset(decoded):
            return ToolResult.model_validate(decoded)

        text = value.strip()
        if text.lower().startswith(("error:", "failed:", "http error:")):
            return failure_result(
                "internal_error",
                code="legacy_tool_error",
                message=text,
                tool_name=tool_name,
            )
        return success_result(
            {"text": value},
            tool_name=tool_name,
            metadata={"legacy": True},
        )

    return success_result(
        {"value": value},
        tool_name=tool_name,
        metadata={"legacy": True},
    )


def is_tool_failure(value: Any) -> bool:
    """Return whether a tool result should fail the current task."""

    result = parse_tool_result(value)
    return result.status in {
        "empty",
        "invalid_input",
        "http_error",
        "timeout",
        "blocked",
        "parse_error",
        "internal_error",
    }
