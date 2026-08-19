import re
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry


class TextSummarizerInput(BaseModel):
    text: str = Field(description="The article text or long narrative content to summarize.")
    max_bullet_points: int = Field(default=5, description="Maximum number of key bullet points to extract.")


@ToolRegistry.register_tool(name="text_summarizer")
@tool("text_summarizer", args_schema=TextSummarizerInput)
def text_summarizer(text: str, max_bullet_points: int = 5) -> str:
    """
    Summarizes long articles, news text, or raw document content into concise key bullet points.
    Use this to condense large text inputs into essential takeaways.
    """
    if not text or len(text.strip()) == 0:
        return "Error: Provided text is empty."

    # Clean text and split into sentences
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 15]

    if not sentences:
        return f"Summary (Raw):\n- {text[:300]}..."

    # Select representative sentences across the text
    selected_bullets = []
    step = max(1, len(sentences) // max_bullet_points)
    for i in range(0, len(sentences), step):
        if len(selected_bullets) < max_bullet_points:
            selected_bullets.append(sentences[i])

    formatted_bullets = "\n".join([f"- {bullet}" for bullet in selected_bullets])

    return (
        f"=== TEXT SUMMARY ({len(selected_bullets)} Bullet Points) ===\n"
        f"{formatted_bullets}\n"
        f"==========================================================="
    )
