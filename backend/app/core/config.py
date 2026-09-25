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
    ENABLE_MONGODB: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Authentication and deployment policy
    AUTH_SIGNING_SECRET: str = "development-only-change-me"
    ACCESS_TOKEN_TTL: int = 3600
    CORS_ORIGINS: str = "http://localhost:5173"
    LLM_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "qwen3:8b"
    ARTIFACT_ROOT: str = "workspace_data"
    MAX_RUN_DURATION: int = 3600
    MAX_TASK_CONCURRENCY: int = 1
    # Opt-in internal instrumentation for local benchmark/evaluation runs only.
    ENABLE_EXECUTION_BENCHMARK_METRICS: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
