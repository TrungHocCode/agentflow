import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings


class MongoDB:
    client: AsyncIOMotorClient = None
    db = None

db_mongo = MongoDB()

def get_mongo_db():
    if os.getenv("TESTING", "").lower() == "true":
        return None
    if db_mongo.client is None:

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        db_mongo.client = AsyncIOMotorClient(
            settings.MONGO_URL,
            io_loop=loop,
            serverSelectionTimeoutMS=2000
        )
        db_mongo.db = db_mongo.client[settings.MONGO_DB]
    return db_mongo.db


async def close_mongo_connection():
    if db_mongo.client:
        db_mongo.client.close()
        db_mongo.client = None
        db_mongo.db = None
