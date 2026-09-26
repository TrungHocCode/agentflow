"""Fixtures for serial unittest runs; never use the developer's artifact directory."""

import os
import tempfile
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator
from unittest import TestCase
from unittest.mock import patch


@contextmanager
def isolated_workspace() -> Iterator[Path]:
    """Restore cwd before deleting only the directory allocated by this fixture."""
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="agentflow-test-") as directory:
        root = Path(directory).resolve()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"ARTIFACT_ROOT": str(root / "workspace_data")}))
            for name in ("markdown_report_generator_tool", "chart_generator_tool"):
                module = sys.modules.get(f"app.execution.tools.{name}")
                if module is not None:
                    stack.enter_context(patch.object(module, "WORKSPACE_DIR", str(root / "workspace_data")))
            os.chdir(root)
            try:
                yield root
            finally:
                os.chdir(previous)


def use_test_adapters(case: TestCase) -> None:
    """Set the test switch per test, including restoration after setup failures."""
    case.enterContext(patch.dict(os.environ, {"TESTING": "true"}))
