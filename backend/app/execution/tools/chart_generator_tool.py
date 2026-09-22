"""Validated chart artifact generation for research outputs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from html import escape
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import failure_result, success_result


WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "workspace_data"))
MAX_POINTS = 100


class ChartGeneratorInput(BaseModel):
    title: str = Field(min_length=1, max_length=300, description="Human-readable chart title.")
    labels: list[str] = Field(min_length=1, max_length=MAX_POINTS)
    values: list[float] = Field(min_length=1, max_length=MAX_POINTS)
    chart_type: Literal["bar", "line"] = "bar"
    filename: str = Field(default="research_chart", max_length=160)
    source_urls: list[str] = Field(default_factory=list, max_length=100)


def _safe_filename(filename: str) -> str:
    value = os.path.basename(filename).replace("\x00", "")
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    return value[:120] or "research_chart"


def _svg(title: str, labels: list[str], values: list[float], chart_type: str) -> str:
    width, height = 900, 520
    chart_left, chart_bottom, chart_width, chart_height = 80, 430, 760, 320
    minimum = min(min(values), 0.0)
    maximum = max(max(values), 0.0)
    value_range = max(maximum - minimum, 1.0)

    def point(index: int, value: float) -> tuple[float, float]:
        x = chart_left + (index * chart_width / max(len(values) - 1, 1))
        y = chart_bottom - ((value - minimum) / value_range * chart_height)
        return x, y

    points = [point(index, value) for index, value in enumerate(values)]
    zero_y = chart_bottom - ((0 - minimum) / value_range * chart_height)
    elements = [
        f'<rect width="{width}" height="{height}" fill="#f5ead7"/>',
        f'<text x="40" y="42" font-family="Georgia" font-size="24" fill="#3e3028">{escape(title)}</text>',
        f'<line x1="{chart_left}" y1="{zero_y:.1f}" x2="{chart_left + chart_width}" y2="{zero_y:.1f}" stroke="#6d5848"/>',
        f'<line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_left}" y2="{chart_bottom - chart_height}" stroke="#6d5848"/>',
    ]
    if chart_type == "bar":
        bar_width = chart_width / max(len(values), 1) * 0.6
        for index, (x, y) in enumerate(points):
            bar_x = chart_left + index * chart_width / max(len(values), 1) + bar_width * 0.33
            bar_top = min(y, zero_y)
            bar_height = abs(zero_y - y)
            elements.append(
                f'<rect x="{bar_x:.1f}" y="{bar_top:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="#a85d3d"/>'
            )
    else:
        elements.append(
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="#2f6f6d" stroke-width="4"/>'
        )
        elements.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#2f6f6d"/>' for x, y in points)
    for index, label in enumerate(labels):
        x = chart_left + (index * chart_width / max(len(labels) - 1, 1))
        elements.append(
            f'<text x="{x:.1f}" y="480" text-anchor="middle" font-size="12" fill="#3e3028">{escape(label)}</text>'
        )
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 520">' + "".join(elements) + "</svg>"


@ToolRegistry.register_tool(name="chart_generator")
@tool("chart_generator", args_schema=ChartGeneratorInput)
def chart_generator(
    title: str,
    labels: list[str],
    values: list[float],
    chart_type: Literal["bar", "line"] = "bar",
    filename: str = "research_chart",
    source_urls: list[str] | None = None,
) -> str:
    """Generate validated JSON/SVG chart artifacts with source provenance."""

    if len(labels) != len(values):
        return failure_result(
            "invalid_input",
            code="chart_length_mismatch",
            message="labels and values must have the same length.",
            tool_name="chart_generator",
        ).to_json()
    if not labels or any(not str(label).strip() for label in labels):
        return failure_result(
            "invalid_input",
            code="invalid_chart_labels",
            message="Chart labels must be non-empty.",
            tool_name="chart_generator",
        ).to_json()
    if any(not math.isfinite(float(value)) for value in values):
        return failure_result(
            "invalid_input",
            code="non_finite_chart_value",
            message="Chart values must be finite numbers.",
            tool_name="chart_generator",
        ).to_json()

    try:
        charts_dir = os.path.join(WORKSPACE_DIR, "charts")
        os.makedirs(charts_dir, exist_ok=True)
        base = _safe_filename(filename)
        if base.endswith(".json"):
            base = base[:-5]
        clean_values = [float(value) for value in values]
        spec = {
            "title": title,
            "chart_type": chart_type,
            "labels": labels,
            "values": clean_values,
            "source_urls": source_urls or [],
        }
        spec_path = os.path.join(charts_dir, f"{base}.json")
        svg_path = os.path.join(charts_dir, f"{base}.svg")
        with open(spec_path, "w", encoding="utf-8") as stream:
            json.dump(spec, stream, ensure_ascii=False, indent=2)
        with open(svg_path, "w", encoding="utf-8") as stream:
            stream.write(_svg(title, labels, clean_values, chart_type))
        with open(svg_path, "rb") as stream:
            checksum = hashlib.sha256(stream.read()).hexdigest()
        return success_result(
            {
                "message": "Chart generated successfully.",
                "svg_path": svg_path,
                "spec_path": spec_path,
                "content_hash": checksum,
                "points": len(values),
                "source_urls": source_urls or [],
            },
            tool_name="chart_generator",
            metadata={"artifact_type": "chart", "chart_type": chart_type},
        ).to_json()
    except Exception as exc:
        return failure_result(
            "internal_error",
            code="chart_generation_failed",
            message=str(exc),
            tool_name="chart_generator",
        ).to_json()
