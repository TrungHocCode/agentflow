import sys
import subprocess
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry

class PythonExecutorInput(BaseModel):
    code: str = Field(description="The Python code to execute. Print any outputs you need to see.")

@ToolRegistry.register_tool(name="python_executor")
@tool("python_executor", args_schema=PythonExecutorInput)
def python_executor(code: str) -> str:
    """
    Executes Python code in a safe subprocess and returns stdout/stderr.
    Use this for calculations, data analysis, or running scripts.
    """
    try:
        # Run in a subprocess with a timeout of 15 seconds
        res = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=15
        )
        if res.returncode == 0:
            return res.stdout if res.stdout else "Success (No output)."
        else:
            return f"Error (Exit code {res.returncode}):\n{res.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Execution timed out (max 15s)."
    except Exception as e:
        return f"Error executing Python code: {str(e)}"
