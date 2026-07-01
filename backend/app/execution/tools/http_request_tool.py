import json
import requests
from typing import Optional, Dict
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry

class HttpRequestInput(BaseModel):
    url: str = Field(description="The URL to send the HTTP request to (e.g. 'https://api.github.com/users/octocat').")
    method: str = Field(default="GET", description="The HTTP method to use (GET, POST, PUT, DELETE).")
    headers: Optional[Dict[str, str]] = Field(default=None, description="Optional HTTP headers dictionary.")
    data: Optional[str] = Field(default=None, description="Optional raw data payload string or JSON-formatted string.")

@ToolRegistry.register_tool(name="http_request")
@tool("http_request", args_schema=HttpRequestInput)
def http_request(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None, data: Optional[str] = None) -> str:
    """
    Sends an HTTP request to a target URL and returns the response status and content.
    Use this to trigger external APIs, fetch JSON web resources, or query webhook endpoints.
    """
    method = method.upper()
    try:
        # Load JSON data payload if possible
        payload = None
        if data:
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = data

        res = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=payload if isinstance(payload, dict) else None,
            data=payload if isinstance(payload, str) else None,
            timeout=10
        )

        try:
            # Try formatting output nicely if it is JSON
            content = json.dumps(res.json(), indent=2)
        except Exception:
            content = res.text

        return f"HTTP Status: {res.status_code}\nResponse Body:\n{content}"
    except Exception as e:
        return f"Error executing HTTP request: {str(e)}"
