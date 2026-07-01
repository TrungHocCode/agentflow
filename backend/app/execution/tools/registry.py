import os
import pkgutil
import importlib
from app.execution.tools.base import ToolRegistry

_discovered = False

def autodiscover_tools(force: bool = False):
    """
    Dynamically scans the tools directory and imports any submodules ending in '_tool.py'.
    This automatically triggers their @ToolRegistry.register_tool decorators.
    """
    global _discovered
    if _discovered and not force:
        return

    package_dir = os.path.dirname(__file__)
    
    for _, module_name, _ in pkgutil.iter_modules([package_dir]):
        if module_name.endswith("_tool"):
            full_module_name = f"app.execution.tools.{module_name}"
            try:
                importlib.import_module(full_module_name)
            except Exception as e:
                # We print it to stderr/logs. In actual FastApi runtime, this will go to logs.
                print(f"Error autodiscovering tool '{full_module_name}': {str(e)}")
                
    _discovered = True

# Automatically run autodiscover on module load
autodiscover_tools()
