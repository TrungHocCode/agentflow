import unittest

from evaluation.http_log_metrics import normalize_route, summarize_http_records


class TestHttpLogMetrics(unittest.TestCase):
    def test_route_normalization_redacts_uuid_path_segments_and_query(self):
        route = normalize_route(
            "/api/v1/runs/123e4567-e89b-42d3-a456-426614174000/events?turn_id=secret"
        )
        self.assertEqual(route, "/api/v1/runs/{id}/events")

    def test_summary_groups_requests_and_uses_defined_window_for_rps(self):
        records = [
            {
                "event_type": "http_request",
                "timestamp": "2026-09-28T10:00:00+00:00",
                "http_method": "GET",
                "http_path": "/api/v1/runs/123e4567-e89b-42d3-a456-426614174000",
                "status_code": 200,
                "duration_ms": 100,
            },
            {
                "event_type": "http_request",
                "timestamp": "2026-09-28T10:00:02+00:00",
                "http_method": "GET",
                "http_path": "/api/v1/runs/123e4567-e89b-42d3-a456-426614174000",
                "status_code": 503,
                "duration_ms": 500,
            },
            {"event_type": "other", "duration_ms": 999},
        ]
        summary = summarize_http_records(records, window_seconds=4)
        self.assertEqual(summary["request_count"], 2)
        self.assertEqual(summary["observed_requests_per_second"], 0.5)
        self.assertEqual(summary["server_error_count"], 1)
        self.assertEqual(summary["latency_ms"]["p50"], 100)
        self.assertEqual(summary["routes"][0]["route"], "/api/v1/runs/{id}")
        self.assertEqual(summary["routes"][0]["observed_requests_per_second"], 0.5)
        self.assertEqual(summary["routes"][0]["client_error_count"], 0)
        self.assertEqual(summary["routes"][0]["successful_request_count"], 1)
        self.assertEqual(summary["routes"][0]["status_counts"], {"200": 1, "503": 1})


if __name__ == "__main__":
    unittest.main()
