"""The Python executor must not inherit deployment secrets or the caller's cwd."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import isolated_workspace

from app.core.config import settings
from app.execution.tools.python_executor_tool import python_executor


class TestPythonExecutorEnvironment(unittest.TestCase):
    def test_subprocess_env_is_allowlisted(self) -> None:
        self.enterContext(isolated_workspace())
        with patch.dict(
            os.environ,
            {
                "AUTH_SIGNING_SECRET": "deployment-secret",
                "POSTGRES_URL": "postgresql+asyncpg://secret/database",
                "REDIS_URL": "redis://secret/0",
            },
        ):
            output = python_executor.invoke({
                "code": (
                    "import os\n"
                    "print('\\n'.join(f'{key}={value}' for key, value in sorted(os.environ.items())))\n"
                ),
            })

        self.assertNotIn("AUTH_SIGNING_SECRET", output)
        self.assertNotIn("POSTGRES_URL", output)
        self.assertNotIn("REDIS_URL", output)
        self.assertIn("PYTHONIOENCODING=utf-8", output)
        self.assertIn(f"HOME={settings.ARTIFACT_ROOT}", output)

    def test_uses_configured_artifact_root_instead_of_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentflow-executor-") as temporary:
            root = Path(temporary) / "configured-artifacts"
            unrelated_cwd = Path(temporary) / "unrelated-cwd"
            unrelated_cwd.mkdir()
            with (
                patch.object(settings, "ARTIFACT_ROOT", str(root)),
                patch("os.getcwd", return_value=str(unrelated_cwd)),
            ):
                output = python_executor.invoke({"code": "import os; print(os.getcwd())"})

            self.assertEqual(Path(output.strip()).resolve(), root.resolve())
            self.assertFalse((unrelated_cwd / "workspace_data").exists())


if __name__ == "__main__":
    unittest.main()
