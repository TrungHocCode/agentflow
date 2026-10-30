"""Regression tests for test-harness safety, without contacting infrastructure."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration_tests.environment import require_integration_environment
from test_support import isolated_workspace, use_test_adapters


class TestHarnessSafety(unittest.TestCase):
    def test_integration_rejects_missing_opt_in_and_test_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                require_integration_environment()
        with patch.dict(os.environ, {"AGENTFLOW_INTEGRATION_TESTS": "1", "ENVIRONMENT": "test"}):
            with self.assertRaises(RuntimeError):
                require_integration_environment()

    def test_integration_accepts_only_dedicated_targets(self) -> None:
        safe = {
            "AGENTFLOW_INTEGRATION_TESTS": "1", "ENVIRONMENT": "local",
            "POSTGRES_URL": "postgresql+asyncpg://test:test@127.0.0.1:55432/agentflow_test",
            "REDIS_URL": "redis://127.0.0.1:56379/15",
        }
        with patch.dict(os.environ, safe):
            require_integration_environment()
            for key, value in (
                ("POSTGRES_URL", "postgresql+asyncpg://test:test@localhost:5433/agentflow_db"),
                ("POSTGRES_URL", "postgresql+asyncpg://test:test@remote:55432/agentflow_test"),
                ("REDIS_URL", "redis://127.0.0.1:6379/0"),
                ("REDIS_URL", "redis://127.0.0.1:56379/15?db=0"),
            ):
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                    with self.assertRaises(RuntimeError):
                        require_integration_environment()

    def test_workspace_restores_cwd_and_preserves_parent_files_after_failure(self) -> None:
        original = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="agentflow-fixture-parent-") as directory:
            parent = Path(directory)
            sentinel = parent / "keep.txt"
            sentinel.write_text("user data", encoding="utf-8")
            os.chdir(parent)
            try:
                with self.assertRaisesRegex(ValueError, "fixture failure"):
                    with isolated_workspace() as temporary:
                        self.assertEqual(Path.cwd(), temporary)
                        (temporary / "generated.txt").write_text("test", encoding="utf-8")
                        raise ValueError("fixture failure")
                self.assertEqual(Path.cwd(), parent)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data")
                self.assertFalse(temporary.exists())
            finally:
                os.chdir(original)

    def test_test_switch_is_restored_by_cleanup(self) -> None:
        from app.core.config import settings

        original = {
            "AUTH_MODE": settings.AUTH_MODE,
            "REPOSITORY_BACKEND": settings.REPOSITORY_BACKEND,
        }
        restored = {
            "ENVIRONMENT": "local",
            "AUTH_MODE": "required",
            "REPOSITORY_BACKEND": "postgres",
        }
        with patch.dict(os.environ, restored):
            case = unittest.TestCase()
            use_test_adapters(case)
            self.assertEqual(os.environ["ENVIRONMENT"], "test")
            self.assertEqual(settings.AUTH_MODE, "disabled")
            self.assertEqual(settings.REPOSITORY_BACKEND, "memory")
            case.doCleanups()
            self.assertEqual(os.environ["ENVIRONMENT"], "local")
            self.assertEqual(settings.AUTH_MODE, original["AUTH_MODE"])
            self.assertEqual(settings.REPOSITORY_BACKEND, original["REPOSITORY_BACKEND"])
