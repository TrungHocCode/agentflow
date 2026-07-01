import os
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry, _get_safe_path

class FileReadInput(BaseModel):
    filename: str = Field(description="The name of the file to read (e.g. 'report.txt').")

@ToolRegistry.register_tool(name="file_reader")
@tool("file_reader", args_schema=FileReadInput)
def file_reader(filename: str) -> str:
    """
    Reads the content of a file in the workspace directory.
    Use this to load dataset files, text documents, or reference files.
    """
    try:
        path = _get_safe_path(filename)
        if not os.path.exists(path):
            return f"Error: File '{filename}' not found in workspace_data."
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"


class FileWriteInput(BaseModel):
    filename: str = Field(description="The name of the file to write (e.g. 'output.txt').")
    content: str = Field(description="The string content to write into the file.")

@ToolRegistry.register_tool(name="file_writer")
@tool("file_writer", args_schema=FileWriteInput)
def file_writer(filename: str, content: str) -> str:
    """
    Writes content to a file in the workspace directory.
    Use this to save outputs, summaries, or data records.
    """
    try:
        path = _get_safe_path(filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote content to file '{filename}' in workspace_data."
    except Exception as e:
        return f"Error writing file: {str(e)}"
