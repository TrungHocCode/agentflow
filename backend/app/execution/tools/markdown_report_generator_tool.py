"""Evidence-aware Markdown report artifact generator."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import failure_result, success_result


WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "workspace_data"))
MAX_REPORT_BYTES = 5 * 1024 * 1024


class SectionItem(BaseModel):
    header: str = Field(min_length=1, max_length=200, description="Section heading/title.")
    content: str = Field(
        min_length=1,
        description=(
            "Developed Markdown analysis grounded in the supplied research. Explain findings and implications, "
            "and cite factual claims inline with the exact source URLs when available."
        ),
    )


class MarkdownReportInput(BaseModel):
    title: str = Field(min_length=1, max_length=300, description="Title of the Markdown report.")
    summary: str | None = Field(
        default=None,
        description="Concise executive summary stating the central answer, strongest findings, and material caveat.",
    )
    sections: list[SectionItem] = Field(
        min_length=1,
        description=(
            "Distinct analytical sections organized around the user's question; synthesize evidence instead of "
            "repeating raw crawl output."
        ),
    )
    filename: str = Field(default="summary_report.md", max_length=200)


def _safe_report_filename(filename: str) -> str:
    basename = os.path.basename(filename).replace("\x00", "")
    basename = re.sub(r"[^a-zA-Z0-9._-]+", "_", basename)
    basename = basename[:120] or "summary_report.md"
    return basename if basename.lower().endswith(".md") else f"{basename}.md"


def _extract_source_urls(content: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>\[\]\\\"']+", content)
    valid: list[str] = []
    for url in urls:
        candidate = url.rstrip(".,;:!?)]}")
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and candidate not in valid:
            valid.append(candidate)
    return valid


def _extract_structured_source_urls(content: str) -> list[str]:
    """Collect URLs from both visible text and a ToolResult source envelope."""

    urls = _extract_source_urls(content)
    try:
        payload: Any = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return urls
    source = payload.get("source") if isinstance(payload, dict) else None
    if isinstance(source, dict):
        for key in ("requested_url", "final_url"):
            value = source.get(key)
            if isinstance(value, str) and value not in urls:
                urls.append(value)
    return urls


def _render_section_content(content: str) -> str:
    """Render structured tool data readably while retaining evidence."""

    try:
        payload: Any = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content
    if not isinstance(payload, dict) or "data" not in payload:
        return content
    if payload.get("ok") is False:
        return f"Tool status: {payload.get('status', 'error')}\n\n{payload.get('error', {})}"
    data = payload.get("data")
    return json.dumps(data, ensure_ascii=False, indent=2)


@ToolRegistry.register_tool(name="markdown_report_generator")
@tool("markdown_report_generator", args_schema=MarkdownReportInput)
def markdown_report_generator(
    title: str,
    sections: list[SectionItem],
    summary: str | None = None,
    filename: str = "summary_report.md",
) -> str:
    """Generate a Markdown report only when at least one evidence section exists."""

    if not sections or not any(section.content.strip() for section in sections):
        return failure_result(
            "empty",
            code="no_report_evidence",
            message="Cannot generate a report without evidence sections.",
            tool_name="markdown_report_generator",
        ).to_json()

    try:
        reports_dir = os.path.join(WORKSPACE_DIR, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        safe_filename = _safe_report_filename(filename)
        file_path = os.path.join(reports_dir, safe_filename)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        rendered_sections = [
            (section.header, _render_section_content(section.content), section.content)
            for section in sections
        ]
        source_urls: list[str] = []
        for _, content, original_content in rendered_sections:
            for url in _extract_structured_source_urls(original_content) + _extract_source_urls(content):
                if url not in source_urls:
                    source_urls.append(url)

        md_content = [
            f"# {title}\n",
            f"> **Generated by AgentFlow Platform** | *{now_str}*\n",
            "---\n",
        ]
        if summary:
            md_content.append(f"## Executive Summary\n\n{summary}\n\n---\n")
        md_content.append("## Table of Contents\n")
        for index, (header, _, _) in enumerate(rendered_sections, 1):
            anchor = re.sub(r"[^a-z0-9-]+", "-", header.lower()).strip("-")
            md_content.append(f"{index}. [{header}](#{anchor})")
        md_content.append("\n---\n")
        for header, content, _ in rendered_sections:
            md_content.append(f"## {header}\n\n{content}\n\n")
        if source_urls:
            md_content.append("## Sources\n\n")
            md_content.extend(f"- {url}\n" for url in source_urls)

        full_text = "\n".join(md_content)
        if len(full_text.encode("utf-8")) > MAX_REPORT_BYTES:
            return failure_result(
                "blocked",
                code="report_too_large",
                message=f"Report exceeds the {MAX_REPORT_BYTES} byte limit.",
                tool_name="markdown_report_generator",
            ).to_json()

        with open(file_path, "w", encoding="utf-8") as stream:
            stream.write(full_text)
        relative_path = os.path.relpath(file_path, WORKSPACE_DIR).replace(os.sep, "/")
        return success_result(
            {
                "message": "Successfully generated Markdown report!",
                "file_path": file_path,
                "relative_path": f"workspace_data/{relative_path}",
                "characters": len(full_text),
                "sections": len(rendered_sections),
                "source_urls": source_urls,
            },
            tool_name="markdown_report_generator",
            metadata={"artifact_type": "markdown_report", "source_count": len(source_urls)},
        ).to_json()
    except Exception as exc:
        return failure_result(
            "internal_error",
            code="report_generation_failed",
            message=str(exc),
            tool_name="markdown_report_generator",
        ).to_json()
