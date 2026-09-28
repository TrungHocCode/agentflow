import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.middleware import RequestBodyLimitMiddleware


class RequestBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_chunked_body_before_calling_application(self) -> None:
        called = False
        messages = [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
        responses = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            responses.append(message)

        async def app(scope, receive, send):
            nonlocal called
            called = True

        middleware = RequestBodyLimitMiddleware(app, max_bytes=5)
        await middleware({"type": "http", "headers": []}, receive, send)

        self.assertFalse(called)
        self.assertEqual(responses[0]["status"], 413)

    async def test_passes_body_within_limit_to_application(self) -> None:
        received = bytearray()
        messages = [{"type": "http.request", "body": b"12345", "more_body": False}]
        responses = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            responses.append(message)

        async def app(scope, app_receive, app_send):
            message = await app_receive()
            received.extend(message["body"])

        await RequestBodyLimitMiddleware(app, max_bytes=5)(
            {"type": "http", "headers": []}, receive, send
        )
        self.assertEqual(received, b"12345")
        self.assertEqual(responses, [])


if __name__ == "__main__":
    unittest.main()
