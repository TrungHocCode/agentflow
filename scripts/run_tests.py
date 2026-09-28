"""Run isolated unit tests or explicitly configured real-infrastructure tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import unittest
import traceback
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "backend"), str(ROOT)]

from test_support import isolated_workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("unit", "integration"))
    args = parser.parse_args()
    attempts: list[str] = []

    def blocked(*_args: object, **_kwargs: object) -> None:
        attempts.append("Unmocked network transport invoked\n" + "".join(traceback.format_stack(limit=8)))
        raise AssertionError(attempts[-1])

    with ExitStack() as stack:
        stack.enter_context(isolated_workspace())
        if args.suite == "unit":
            stack.enter_context(patch.dict(os.environ, {
                "TESTING": "true", "ENABLE_MONGODB": "false",
                "AGENTFLOW_RUN_LIVE_OLLAMA_TESTS": "0",
                "AGENTFLOW_CRAWLER_BROWSER_ENABLED": "false",
                "AUTH_SIGNING_SECRET": "unit-tests-only-not-a-deployment-secret",
                "POSTGRES_URL": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unit_unused",
                "REDIS_URL": "redis://127.0.0.1:1/15",
            }))
            # DNS is a fixture, not a public network dependency. Specific SSRF tests
            # replace this with their own private/public address fixtures.
            stack.enter_context(patch("socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            ]))
            for target in (
                "requests.sessions.Session.request",
                "httpx.HTTPTransport.handle_request",
                "httpx.AsyncHTTPTransport.handle_async_request",
            ):
                stack.enter_context(patch(target, side_effect=blocked))
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        else:
            from integration_tests.environment import require_integration_environment
            require_integration_environment()
            suite = unittest.defaultTestLoader.discover(str(ROOT / "integration_tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if attempts:
            print(f"FAILED: {len(attempts)} unmocked network attempts (including caught errors).", file=sys.stderr)
            print("\n".join(attempts), file=sys.stderr)
        return 0 if result.wasSuccessful() and not attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
