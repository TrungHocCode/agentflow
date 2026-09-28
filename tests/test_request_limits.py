import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

class RedisRateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_counter_rejects_after_limit(self) -> None:
        from app.infrastructure.redis.rate_limiter import RedisRateLimiter
        from app.shared.errors import RateLimitExceeded

        class FakeRedis:
            def __init__(self):
                self.counts = {}
                self.keys = []

            async def eval(self, script, key_count, key, window_seconds):
                self.keys.append(key)
                self.counts[key] = self.counts.get(key, 0) + 1
                return self.counts[key]

        client = FakeRedis()
        with patch(
            "app.infrastructure.redis.rate_limiter.get_redis",
            AsyncMock(return_value=client),
        ):
            limiter = RedisRateLimiter()
            await limiter.consume("chat", "user@example.com", 1, 60)
            with self.assertRaises(RateLimitExceeded):
                await limiter.consume("chat", "user@example.com", 1, 60)

        self.assertEqual(client.keys[0], client.keys[1])
        self.assertNotIn("user@example.com", client.keys[0])

    async def test_redis_failure_fails_closed_with_dependency_error(self) -> None:
        from app.infrastructure.redis.rate_limiter import RedisRateLimiter
        from app.shared.errors import PersistenceError

        with patch(
            "app.infrastructure.redis.rate_limiter.get_redis",
            AsyncMock(side_effect=OSError("redis down")),
        ):
            with self.assertRaises(PersistenceError):
                await RedisRateLimiter().consume("run", "user-id", 1, 60)


if __name__ == "__main__":
    unittest.main()
