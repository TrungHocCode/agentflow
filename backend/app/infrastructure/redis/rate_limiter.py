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


_RELEASE = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count > 0 then
  return redis.call('DECR', KEYS[1])
end
return 0
"""


class RedisRateLimiter(RateLimiter):
    """Apply one atomic fixed-window counter shared by API processes."""

    def __init__(self, key_prefix: str = "agentflow:rate:v1:") -> None:
        self.key_prefix = key_prefix

    def _key(self, scope: str, identifier: str) -> str:
        safe_scope = "".join(character for character in scope if character.isalnum() or character in "-_")
        subject = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}{safe_scope}:{subject}"

    async def consume(
        self,
        scope: str,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        key = self._key(scope, identifier)
        try:
            client = await get_redis()
            count = int(await client.eval(_INCREMENT_AND_EXPIRE, 1, key, window_seconds))
        except Exception as exc:
            raise PersistenceError("Request protection is temporarily unavailable.") from exc
        if count > limit:
            raise RateLimitExceeded("Too many requests. Please wait and try again.")

    async def release(self, scope: str, identifier: str) -> None:
        client = await get_redis()
        await client.eval(_RELEASE, 1, self._key(scope, identifier))


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

    async def release(self, scope: str, identifier: str) -> None:
        return None
