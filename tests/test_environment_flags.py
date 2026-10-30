"""Environment-derived runtime flags must fail closed for production."""

import os
import unittest
from unittest.mock import patch

from app.core.config import Settings, validate_runtime_settings


class TestEnvironmentFlags(unittest.TestCase):
    def make_settings(self, **overrides):
        with patch.dict(os.environ, {}, clear=True):
            return Settings(_env_file=None, **overrides)

    def test_test_environment_defaults_to_disabled_auth_and_memory_repository(self) -> None:
        settings = self.make_settings(ENVIRONMENT="test")

        self.assertEqual(settings.AUTH_MODE, "disabled")
        self.assertEqual(settings.REPOSITORY_BACKEND, "memory")

    def test_non_test_environment_defaults_to_required_auth_and_postgres(self) -> None:
        settings = self.make_settings(ENVIRONMENT="local")

        self.assertEqual(settings.AUTH_MODE, "required")
        self.assertEqual(settings.REPOSITORY_BACKEND, "postgres")

    def test_explicit_overrides_are_respected(self) -> None:
        settings = self.make_settings(
            ENVIRONMENT="test",
            AUTH_MODE="required",
            REPOSITORY_BACKEND="postgres",
        )

        self.assertEqual(settings.AUTH_MODE, "required")
        self.assertEqual(settings.REPOSITORY_BACKEND, "postgres")

    def test_production_rejects_disabled_auth(self) -> None:
        settings = self.make_settings(
            ENVIRONMENT="prod",
            AUTH_MODE="disabled",
            REPOSITORY_BACKEND="postgres",
            AUTH_SIGNING_SECRET="x" * 32,
            CORS_ORIGINS="https://agentflow.example",
        )

        with self.assertRaisesRegex(ValueError, "AUTH_MODE"):
            validate_runtime_settings(settings)

    def test_production_rejects_memory_repository(self) -> None:
        settings = self.make_settings(
            ENVIRONMENT="production",
            AUTH_MODE="required",
            REPOSITORY_BACKEND="memory",
            AUTH_SIGNING_SECRET="x" * 32,
            CORS_ORIGINS="https://agentflow.example",
        )

        with self.assertRaisesRegex(ValueError, "REPOSITORY_BACKEND"):
            validate_runtime_settings(settings)


if __name__ == "__main__":
    unittest.main()
