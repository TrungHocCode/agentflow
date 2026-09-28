import asyncio
import io
import json
import logging
import unittest

from app.shared.observability import (
    JsonLogFormatter,
    RequestCorrelationMiddleware,
    bind_context,
)


class TestStructuredLogging(unittest.TestCase):
    def test_formatter_redacts_credentials_and_adds_correlation_fields(self) -> None:
        record = logging.LogRecord(
            name="agentflow.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=(
                "Authorization: Bearer bearer-secret password=pass-secret "
                "token=token-secret api_key=key-secret "
                "https://user:url-secret@example.test/path?access_token=query-secret"
            ),
            args=(),
            exc_info=None,
        )
        record.error_id = "error-123"
        record.error_code = "dependency_unavailable"

        with bind_context(request_id="request-123", run_id="run-123", llm_call_id="llm-123"):
            payload = json.loads(JsonLogFormatter().format(record))

        for secret in (
            "bearer-secret",
            "pass-secret",
            "token-secret",
            "key-secret",
            "url-secret",
            "query-secret",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, payload["message"])
        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["llm_call_id"], "llm-123")
        self.assertEqual(payload["error_id"], "error-123")
        self.assertEqual(payload["error_code"], "dependency_unavailable")

    def test_request_middleware_returns_and_logs_request_id(self) -> None:
        log_stream = io.StringIO()
        capture_handler = logging.StreamHandler(log_stream)
        capture_handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("agentflow.http")
        previous_handlers = logger.handlers[:]
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.handlers = [capture_handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False

        sent_messages = []

        async def downstream(scope, receive, send):
            del scope, receive
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent_messages.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/runs",
            "headers": [(b"x-request-id", b"client-request-123")],
            "state": {},
        }
        try:
            asyncio.run(RequestCorrelationMiddleware(downstream)(scope, receive, send))
        finally:
            logger.handlers = previous_handlers
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        response_start = next(message for message in sent_messages if message["type"] == "http.response.start")
        response_headers = dict(response_start["headers"])
        self.assertEqual(response_headers[b"x-request-id"], b"client-request-123")
        self.assertEqual(scope["state"]["request_id"], "client-request-123")
        log_record = json.loads(log_stream.getvalue())
        self.assertEqual(log_record["request_id"], "client-request-123")
        self.assertEqual(log_record["http_method"], "GET")
        self.assertEqual(log_record["http_path"], "/api/v1/runs")
        self.assertEqual(log_record["status_code"], 204)


if __name__ == "__main__":
    unittest.main()
