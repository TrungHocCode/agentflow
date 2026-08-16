import redis.asyncio as aioredis
from app.core.config import settings

class RedisClientWrapper:
    client = None

redis_wrapper = RedisClientWrapper()

async def get_redis():
    if redis_wrapper.client is None:
        redis_wrapper.client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_wrapper.client

async def close_redis_connection():
    if redis_wrapper.client:
        await redis_wrapper.client.aclose()
        redis_wrapper.client = None
