from fastapi import APIRouter
from app.db.postgres_client import engine
from app.db.mongo_client import get_mongo_db
from app.db.redis_client import get_redis
from sqlalchemy import text

router = APIRouter()

@router.get("/health")
async def health_check():
    status = {"status": "ok", "databases": {}}

    # Postgres check
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["databases"]["postgres"] = "online"
    except Exception as e:
        status["databases"]["postgres"] = f"offline: {str(e)}"
        status["status"] = "degraded"

    # Mongo check
    try:
        db = get_mongo_db()
        await db.command("ping")
        status["databases"]["mongodb"] = "online"
    except Exception as e:
        status["databases"]["mongodb"] = f"offline: {str(e)}"
        status["status"] = "degraded"

    # Redis check
    try:
        redis = await get_redis()
        await redis.ping()
        status["databases"]["redis"] = "online"
    except Exception as e:
        status["databases"]["redis"] = f"offline: {str(e)}"
        status["status"] = "degraded"

    return status
