import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # PostgreSQL
    POSTGRES_URL: str = "postgresql+asyncpg://agentflow:agentflow_password@localhost:5433/agentflow_db"

    # MongoDB
    MONGO_URL: str = "mongodb://agentflow:agentflow_password@localhost:27017/agentflow_runs?authSource=admin"
    MONGO_DB: str = "agentflow_runs"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
