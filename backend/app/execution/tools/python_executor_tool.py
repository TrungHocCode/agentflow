import sys
import subprocess
import os
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.core.config import settings
from app.execution.tools.base import ToolRegistry


MAX_CODE_CHARS = 100_000
MAX_OUTPUT_CHARS = 200_000
EXECUTION_TIMEOUT_SECONDS = 15

class PythonExecutorInput(BaseModel):
    code: str = Field(description="The Python code to execute. Print any outputs you need to see.")

@ToolRegistry.register_tool(name="python_executor")
@tool("python_executor", args_schema=PythonExecutorInput)
def python_executor(code: str) -> str:
    """
    Executes Python code in a subprocess without an OS/container sandbox.

    The environment is allowlisted, but only timeouts and output caps constrain
    execution; do not treat this as a security isolation boundary.
    """
    try:
        if not code or len(code) > MAX_CODE_CHARS:
            return f"Error: Code must be between 1 and {MAX_CODE_CHARS} characters."
        workspace_dir = settings.ARTIFACT_ROOT
        os.makedirs(workspace_dir, exist_ok=True)
        res = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT_SECONDS,
            cwd=workspace_dir,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
                "HOME": workspace_dir,
            },
        )
        if res.returncode == 0:
            output = res.stdout if res.stdout else "Success (No output)."
            return output[:MAX_OUTPUT_CHARS]
        else:
            return f"Error (Exit code {res.returncode}):\n{res.stderr[:MAX_OUTPUT_CHARS]}"
    except subprocess.TimeoutExpired:
        return f"Error: Execution timed out (max {EXECUTION_TIMEOUT_SECONDS}s)."
    except Exception as e:
        return f"Error executing Python code: {str(e)}"
