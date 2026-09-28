"""Ports for cross-cutting system controls."""

from typing import Protocol


class RateLimiter(Protocol):
    async def consume(
        self,
        scope: str,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        """Consume one request allowance or reject the request."""
        ...
