"""Stable result contracts consumed by the dashboard and artifact layer."""

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Evidence reference attached to a research result."""

    source_url: str
    source_title: str | None = None
    excerpt: str | None = None
    source_type: str = "web"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChartSpec(BaseModel):
    """Chart-ready data; the frontend should not parse natural language."""

    chart_type: Literal["line", "bar", "area", "pie", "scatter", "table"]
    title: str
    data: List[Dict[str, Any]] = Field(default_factory=list)
    x_axis: str | None = None
    y_axis: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    """Structured output produced by one task or a run projection."""

    result_id: str
    run_id: str
    task_id: str | None = None
    result_type: Literal[
        "raw_data",
        "normalized_data",
        "summary",
        "comparison",
        "chart_spec",
        "report",
        "file",
    ]
    content: Any = None
    citations: List[Citation] = Field(default_factory=list)
    artifact_id: str | None = None
    schema_version: str = "1"
    metadata: Dict[str, Any] = Field(default_factory=dict)
