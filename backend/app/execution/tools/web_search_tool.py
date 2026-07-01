import re
import requests
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry

class WebSearchInput(BaseModel):
    query: str = Field(description="The query string to search on the web.")

@ToolRegistry.register_tool(name="web_search")
@tool("web_search", args_schema=WebSearchInput)
def web_search(query: str) -> str:
    """
    Search the web for real-time information and returns key snippets.
    Use this to look up facts, documentation, or news.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    try:
        res = requests.post(url, headers=headers, data={"q": query}, timeout=10)
        if res.status_code != 200:
            return f"Error: Failed to fetch search results (HTTP {res.status_code})."
        
        # Scrape result snippets using regex to avoid external dependency issues
        bodies = re.findall(r'<div class="result__body">(.*?)</div>', res.text, re.DOTALL)
        results = []
        for body in bodies[:5]:
            # Extract description snippet
            snippet_match = re.search(r'<a class="result__snippet"[^>]*>(.*?)</a>', body, re.DOTALL)
            snippet = snippet_match.group(1) if snippet_match else ""
            snippet = re.sub(r'<[^>]*>', '', snippet).strip()
            
            # Extract link
            link_match = re.search(r'<a class="result__url"[^>]*>(.*?)</a>', body, re.DOTALL)
            link = link_match.group(1) if link_match else ""
            link = re.sub(r'<[^>]*>', '', link).strip()
            
            if snippet:
                results.append(f"- {link}: {snippet}")
        
        if not results:
            # Fallback scraping
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', res.text, re.DOTALL)
            for i, snip in enumerate(snippets[:5]):
                clean_snip = re.sub(r'<[^>]*>', '', snip).strip()
                results.append(f"- Result {i+1}: {clean_snip}")
                
        if not results:
            return "No search results found."
        return "\n".join(results)
    except Exception as e:
        return f"Error executing web search: {str(e)}"
