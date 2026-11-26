"""Read-only single-page fetch: one public page's text, no listing expansion."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import failure_result
from app.execution.tools.crawler.orchestrator import crawl_url
from app.execution.tools.network_policy import validate_external_url
from app.execution.tools.news_crawler_tool import _extract_page, normalize_news_url


class FetchPageInput(BaseModel):
    url: str = Field(description="The public page URL to read.")


@ToolRegistry.register_tool(name="fetch_page")
@tool("fetch_page", args_schema=FetchPageInput)
def fetch_page(url: str) -> str:
    """Fetch one public web page and return its title and text as JSON (no linked pages are followed)."""

    normalized_url, error = normalize_news_url(url)
    if error or not normalized_url:
        return failure_result("invalid_input", code="invalid_url", message=error or "URL is invalid.",
                              tool_name="fetch_page").to_json()
    safe_url, network_error = validate_external_url(normalized_url)
    if network_error or not safe_url:
        return failure_result("blocked", code="outbound_url_blocked",
                              message=network_error or "URL is not allowed.", tool_name="fetch_page").to_json()
    return crawl_url(safe_url, "static", extract_page=_extract_page)
