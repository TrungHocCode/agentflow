"""Fail closed before imports/migrations can connect to a developer database."""

import os
from urllib.parse import urlsplit


def require_integration_environment() -> None:
    """Accept only the dedicated loopback test services; never infer defaults."""
    if os.getenv("AGENTFLOW_INTEGRATION_TESTS") != "1" or os.getenv("TESTING", "").lower() != "false":
        raise RuntimeError("Integration requires AGENTFLOW_INTEGRATION_TESTS=1 and TESTING=false.")
    for name, scheme, port, path in (
        ("POSTGRES_URL", "postgresql+asyncpg", 55432, "/agentflow_test"),
        ("REDIS_URL", "redis", 56379, "/15"),
    ):
        url = urlsplit(os.getenv(name, ""))
        if (url.scheme != scheme or url.hostname not in {"localhost", "127.0.0.1"}
                or url.port != port or url.path != path or url.query or url.fragment):
            raise RuntimeError(f"{name} must point to the dedicated loopback test service on port {port}.")
