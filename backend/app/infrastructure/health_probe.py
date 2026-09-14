"""Infrastructure probes for PostgreSQL, optional MongoDB and Redis."""

import os
from typing import Any, Dict

from sqlalchemy import text

from app.db.mongo_client import get_mongo_db
from app.db.postgres_client import engine
from app.db.redis_client import get_redis
from app.modules.system.health import HealthProbe


class DatabaseHealthProbe(HealthProbe):
    """Check infrastructure dependencies and return a non-throwing report."""

    async def check(self) -> Dict[str, Any]:
        database_status: Dict[str, str] = {}
        await self._check_postgres(database_status)
        await self._check_mongodb(database_status)
        await self._check_redis(database_status)
        healthy = all(value in {"online", "disabled"} for value in database_status.values())
        return {
            "status": "ok" if healthy else "degraded",
            "databases": database_status,
        }

    @staticmethod
    async def _check_postgres(database_status: Dict[str, str]) -> None:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            database_status["postgres"] = "online"
        except Exception as exc:
            database_status["postgres"] = f"offline: {exc}"

    @staticmethod
    async def _check_mongodb(database_status: Dict[str, str]) -> None:
        if os.getenv("ENABLE_MONGODB", "false").lower() != "true":
            database_status["mongodb"] = "disabled"
            return
        try:
            await get_mongo_db().command("ping")
            database_status["mongodb"] = "online"
        except Exception as exc:
            database_status["mongodb"] = f"offline: {exc}"

    @staticmethod
    async def _check_redis(database_status: Dict[str, str]) -> None:
        try:
            redis = await get_redis()
            await redis.ping()
            database_status["redis"] = "online"
        except Exception as exc:
            database_status["redis"] = f"offline: {exc}"
