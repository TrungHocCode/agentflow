"""Dependency-free chart generation tool for the technology research MVP."""

import hashlib
import json
import os
import re
from typing import List, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry


WORKSPACE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "workspace_data")
)


class ChartGeneratorInput(BaseModel):
    title: str = Field(description="Human-readable chart title.")
    labels: List[str] = Field(min_length=1, description="Labels for each data point.")
    values: List[float] = Field(min_length=1, description="Numeric values aligned with labels.")
    chart_type: Literal["bar", "line"] = "bar"
    filename: str = "research_chart"


def _safe_filename(filename: str) -> str:
    value = os.path.basename(filename)
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    return value[:120] or "research_chart"


def _svg(title: str, labels: List[str], values: List[float], chart_type: str) -> str:
    width, height = 900, 520
    chart_left, chart_bottom, chart_width, chart_height = 80, 430, 760, 320
    maximum = max(max(values), 1)
    points = []
    for index, value in enumerate(values):
        x = chart_left + (index * chart_width / max(len(values) - 1, 1))
        y = chart_bottom - (value / maximum * chart_height)
        points.append((x, y))
    elements = [
        f'<rect width="{width}" height="{height}" fill="#f5ead7"/>',
        f'<text x="40" y="42" font-family="Georgia" font-size="24" fill="#3e3028">{title}</text>',
        f'<line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_left + chart_width}" y2="{chart_bottom}" stroke="#6d5848"/>',
        f'<line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_left}" y2="{chart_bottom - chart_height}" stroke="#6d5848"/>',
    ]
    if chart_type == "bar":
        bar_width = chart_width / max(len(values), 1) * 0.6
        for index, (x, y) in enumerate(points):
            bar_x = chart_left + index * chart_width / max(len(values), 1) + bar_width * 0.33
            elements.append(
                f'<rect x="{bar_x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{chart_bottom - y:.1f}" fill="#a85d3d"/>'
            )
    else:
        elements.append(
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="#2f6f6d" stroke-width="4"/>'
        )
        elements.extend(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#2f6f6d"/>' for x, y in points
        )
    for index, label in enumerate(labels):
        x = chart_left + (index * chart_width / max(len(labels) - 1, 1))
        elements.append(
            f'<text x="{x:.1f}" y="460" text-anchor="middle" font-size="12" fill="#3e3028">{label}</text>'
        )
    elements.append("</svg>")
    return "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 900 520\">" + "".join(elements)


@ToolRegistry.register_tool(name="chart_generator")
@tool("chart_generator", args_schema=ChartGeneratorInput)
def chart_generator(
    title: str,
    labels: List[str],
    values: List[float],
    chart_type: Literal["bar", "line"] = "bar",
    filename: str = "research_chart",
) -> str:
    """Generate a chart specification and a viewable SVG in workspace_data/charts."""

    if len(labels) != len(values):
        return "Error: labels and values must have the same length."
    try:
        charts_dir = os.path.join(WORKSPACE_DIR, "charts")
        os.makedirs(charts_dir, exist_ok=True)
        base = _safe_filename(filename)
        if base.endswith(".json"):
            base = base[:-5]
        spec = {
            "title": title,
            "chart_type": chart_type,
            "labels": labels,
            "values": values,
            "source_data": {"labels": labels, "values": values},
        }
        spec_path = os.path.join(charts_dir, f"{base}.json")
        svg_path = os.path.join(charts_dir, f"{base}.svg")
        with open(spec_path, "w", encoding="utf-8") as stream:
            json.dump(spec, stream, ensure_ascii=False, indent=2)
        with open(svg_path, "w", encoding="utf-8") as stream:
            stream.write(_svg(title, labels, values, chart_type))
        with open(svg_path, "rb") as stream:
            checksum = hashlib.sha256(stream.read()).hexdigest()
        return (
            f"Chart generated successfully. File Path: {svg_path}. "
            f"Chart Spec Path: {spec_path}. Content Hash: {checksum}."
        )
    except Exception as exc:
        return f"Error generating chart: {exc}"
