"""Small ASGI middleware for request resource limits."""

from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before endpoint parsing or model work."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared_size = headers.get(b"content-length")
        if declared_size:
            try:
                if int(declared_size) > self.max_bytes:
                    await self._reject(scope, send)
                    return
            except ValueError:
                await self._reject(scope, send)
                return

        messages: list[dict[str, Any]] = []
        body_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                body_size += len(message.get("body", b""))
                if body_size > self.max_bytes:
                    await self._reject(scope, send)
                    return
                if not message.get("more_body", False):
                    break

        remaining = list(messages)

        async def replay_receive() -> dict[str, Any]:
            if remaining:
                return remaining.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _reject(
        self,
        scope: dict[str, Any],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        error_id = str(uuid.uuid4())
        body = json.dumps(
            {
                "error": {
                    "error_id": error_id,
                    "code": "request_body_too_large",
                    "category": "validation",
                    "message": "The request body exceeds the allowed size.",
                    "retryable": False,
                    "request_id": None,
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
