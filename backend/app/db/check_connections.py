import asyncio
import os
import sys

# Adjust path to import backend modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import text
from app.db.postgres_client import engine
from app.db.mongo_client import get_mongo_db, close_mongo_connection
from app.db.redis_client import get_redis, close_redis_connection

async def check_postgres():
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version();"))
            row = result.fetchone()
            print(f"[OK] PostgreSQL Connected Successfully! Version: {row[0].split(',')[0]}")
            return True
    except Exception as e:
        print(f"[FAIL] PostgreSQL Connection Failed: {e}")
        return False

async def check_mongodb():
    try:
        db = get_mongo_db()
        server_info = await db.command("ping")
        print(f"[OK] MongoDB Connected Successfully! Ping response: {server_info}")
        await close_mongo_connection()
        return True
    except Exception as e:
        print(f"[FAIL] MongoDB Connection Failed: {e}")
        return False

async def check_redis():
    try:
        redis = await get_redis()
        pong = await redis.ping()
        print(f"[OK] Redis Connected Successfully! Ping response: {pong}")
        await close_redis_connection()
        return True
    except Exception as e:
        print(f"[FAIL] Redis Connection Failed: {e}")
        return False

async def main():
    print("=" * 60)
    print("      AGENTFLOW DATABASE CONNECTION HEALTH CHECK")
    print("=" * 60)
    
    pg_ok = await check_postgres()
    mongo_ok = await check_mongodb()
    redis_ok = await check_redis()
    
    print("=" * 60)
    if pg_ok and mongo_ok and redis_ok:
        print(">>> ALL DATABASES ONLINE & OPERATIONAL! READY TO PROCEED. <<<")
    else:
        print(">>> SOME DATABASES ARE OFFLINE. PLEASE START THEM WITH 'docker compose up -d'. <<<")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
