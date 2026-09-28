"""Shared Redis-backed fixed-window request limits."""

import hashlib

from app.db.redis_client import get_redis
from app.modules.system.ports import RateLimiter
from app.shared.errors import RateLimitExceeded, PersistenceError


_INCREMENT_AND_EXPIRE = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class RedisRateLimiter(RateLimiter):
    """Apply one atomic fixed-window counter shared by API processes."""

    def __init__(self, key_prefix: str = "agentflow:rate:v1:") -> None:
        self.key_prefix = key_prefix

    async def consume(
        self,
        scope: str,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        safe_scope = "".join(character for character in scope if character.isalnum() or character in "-_")
        subject = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        key = f"{self.key_prefix}{safe_scope}:{subject}"
        try:
            client = await get_redis()
            count = int(await client.eval(_INCREMENT_AND_EXPIRE, 1, key, window_seconds))
        except Exception as exc:
            raise PersistenceError("Request protection is temporarily unavailable.") from exc
        if count > limit:
            raise RateLimitExceeded("Too many requests. Please wait and try again.")


class NoopRateLimiter(RateLimiter):
    """Test-only adapter; production composition always uses Redis."""

    async def consume(
        self,
        scope: str,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        return None
