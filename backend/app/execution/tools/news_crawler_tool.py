import re
import requests
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry


class SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.paragraphs = []
        self._in_title = False
        self._in_p = False
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True
        elif tag.lower() in ("p", "h1", "h2", "h3"):
            self._in_p = True
            self._current_text = []

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() in ("p", "h1", "h2", "h3"):
            self._in_p = False
            text = " ".join("".join(self._current_text).split())
            if text and len(text) > 20:  # Skip tiny nav snippets
                self.paragraphs.append(text)
            self._current_text = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_p:
            self._current_text.append(data)


class NewsCrawlerInput(BaseModel):
    url: str = Field(description="The news article URL to crawl and extract content from.")


def normalize_news_url(raw_url: str) -> tuple[str | None, str | None]:
    """Normalize common LLM URL formatting and validate HTTP(S) URLs."""

    candidate = str(raw_url or "").strip()
    match = re.search(r"https?://[^\s<>\[\]\\\"']+", candidate)
    if match:
        candidate = match.group(0)
    candidate = candidate.strip("<>").rstrip(".,;:!?)]}")

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "URL must be an absolute HTTP(S) URL."
    return candidate, None


@ToolRegistry.register_tool(name="news_crawler")
@tool("news_crawler", args_schema=NewsCrawlerInput)
def news_crawler(url: str) -> str:
    """
    Crawls a news article or web page URL and extracts the title, headings, and main article paragraphs.
    Use this to fetch live news articles, blog posts, or editorial web content.
    """
    normalized_url, validation_error = normalize_news_url(url)
    if validation_error:
        return f"Error: Invalid news URL '{url}': {validation_error}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AgentFlowNewsCrawler/1.0"
    }

    try:
        res = requests.get(normalized_url, headers=headers, timeout=10)
        if res.status_code != 200:
            return f"Error: Failed to fetch URL '{normalized_url}' (HTTP Status Code: {res.status_code})."

        parser = SimpleHTMLTextExtractor()
        parser.feed(res.text)

        title = parser.title.strip() if parser.title else "Untitled Article"
        body_content = "\n\n".join(parser.paragraphs[:15]) if parser.paragraphs else "No main paragraph text extracted."

        return (
            f"=== CRAWLED ARTICLE ===\n"
            f"URL: {normalized_url}\n"
            f"Title: {title}\n"
            f"Extracted Paragraphs ({len(parser.paragraphs)} paragraphs found):\n\n"
            f"{body_content}\n"
            f"======================="
        )
    except Exception as e:
        return f"Error crawling news URL '{normalized_url}': {str(e)}"
