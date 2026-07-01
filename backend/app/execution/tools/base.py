import os
from typing import Dict, List, Optional
from langchain_core.tools import BaseTool, tool

class ToolRegistry:
    """
    Registry for loading and accessing tools dynamically.
    """
    _registry: Dict[str, BaseTool] = {}

    @classmethod
    def register_tool(cls, name_or_tool=None, name: Optional[str] = None):
        """
        Register a LangChain BaseTool or a python function as a tool.
        Supports decorator usage or direct call:
        - ToolRegistry.register_tool(my_tool)
        - @ToolRegistry.register_tool
        - @ToolRegistry.register_tool(name="custom")
        """
        if name_or_tool is not None and not isinstance(name_or_tool, str):
            if isinstance(name_or_tool, BaseTool):
                tool_name = name or name_or_tool.name
                cls._registry[tool_name] = name_or_tool
                return name_or_tool
            else:
                langchain_tool = tool(name_or_tool)
                tool_name = name or langchain_tool.name
                cls._registry[tool_name] = langchain_tool
                return name_or_tool

        specified_name = name or (name_or_tool if isinstance(name_or_tool, str) else None)

        def decorator(func_or_tool):
            if isinstance(func_or_tool, BaseTool):
                tool_name = specified_name or func_or_tool.name
                cls._registry[tool_name] = func_or_tool
                return func_or_tool
            else:
                langchain_tool = tool(func_or_tool)
                tool_name = specified_name or langchain_tool.name
                cls._registry[tool_name] = langchain_tool
                return func_or_tool
        return decorator

    @classmethod
    def get_tool(cls, name: str) -> BaseTool:
        if name not in cls._registry:
            raise ValueError(f"Tool '{name}' is not registered in ToolRegistry. Make sure autodiscovery is triggered.")
        return cls._registry[name]

    @classmethod
    def list_tools(cls) -> List[str]:
        return list(cls._registry.keys())


def _get_safe_path(filename: str) -> str:
    """
    Resolves filename to a safe sub-directory in workspace to prevent path traversal issues.
    """
    cwd = os.getcwd()
    safe_name = os.path.basename(filename)
    data_dir = os.path.join(cwd, "workspace_data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, safe_name)
