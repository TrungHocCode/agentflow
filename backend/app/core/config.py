from pydantic_settings import BaseSettings
from pydantic import Field, model_validator

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
    LLM_CHAT_MODEL: str = "qwen3:0.6b"
    LLM_PLANNER_MODEL: str = "qwen3:8b"
    LLM_WORKER_MODEL: str = "qwen3:8b"
    # Explicit context policy; estimates are conservative, not provider token counts.
    LLM_CONTEXT_TOKENS: int = Field(default=8192, ge=2048)
    LLM_OUTPUT_TOKENS: int = Field(default=1024, ge=128)
    LLM_CONTEXT_MARGIN_TOKENS: int = Field(default=1024, ge=128)
    RESEARCH_MAP_OUTPUT_TOKENS: int = Field(default=768, ge=128)
    RESEARCH_MAX_CHUNKS: int = Field(default=32, ge=1, le=256)
    RESEARCH_MAX_DOCUMENTS: int = Field(default=8, ge=1, le=64)
    RESEARCH_CHUNK_CHARS: int = Field(default=3000, ge=128, le=16000)
    RESEARCH_MAX_REDUCE_CALLS: int = Field(default=32, ge=1, le=256)
    RESEARCH_MAX_REDUCE_DEPTH: int = Field(default=4, ge=1, le=8)
    ARTIFACT_ROOT: str = "workspace_data"
    MAX_RUN_DURATION: int = Field(default=3600, ge=1, le=86400)
    MAX_RUN_LLM_CALLS: int = Field(default=64, ge=1, le=4096)
    MAX_RUN_ESTIMATED_TOKENS: int = Field(default=262144, ge=1024, le=16777216)
    MAX_TASK_CONCURRENCY: int = 1
    MAX_REQUEST_BODY_BYTES: int = 1_048_576
    MAX_WORKFLOW_STEPS: int = 50
    MAX_TASK_TIMEOUT_SECONDS: int = 600
    MAX_TASK_ITERATIONS: int = 5
    MAX_WORKFLOW_DEFINITION_BYTES: int = 786_432
    MAX_RUN_INPUT_BYTES: int = 262_144
    MAX_REQUEST_METADATA_BYTES: int = 65_536
    # Durable conversation turn worker and replay limits.
    CONVERSATION_TURN_TIMEOUT_SECONDS: int = 900
    CONVERSATION_TURN_MAX_QUEUE_WAIT_SECONDS: int = 1800
    CONVERSATION_TURN_STALE_SECONDS: int = 60
    CONVERSATION_TURN_HEARTBEAT_SECONDS: int = 10
    CONVERSATION_TURN_POLL_SECONDS: float = 1.0
    CONVERSATION_EVENT_POLL_SECONDS: float = 0.5
    CONVERSATION_DELTA_FLUSH_CHARS: int = 160
    CONVERSATION_DELTA_FLUSH_INTERVAL_SECONDS: float = 0.2
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10
    CHAT_RATE_LIMIT_PER_MINUTE: int = 30
    RUN_CREATE_RATE_LIMIT_PER_MINUTE: int = 10
    # Opt-in internal instrumentation for local benchmark/evaluation runs only.
    ENABLE_EXECUTION_BENCHMARK_METRICS: bool = False
    # The subprocess tool is not an OS/container sandbox; keep it disabled unless
    # the operator explicitly accepts that risk for this deployment.
    ENABLE_UNSANDBOXED_PYTHON_EXECUTION: bool = False
    ENABLE_EXTERNAL_SIDE_EFFECT_TOOLS: bool = False

    @model_validator(mode="after")
    def validate_context_policy(self) -> "Settings":
        if self.LLM_OUTPUT_TOKENS + self.LLM_CONTEXT_MARGIN_TOKENS >= self.LLM_CONTEXT_TOKENS:
            raise ValueError("Output reservation and safety margin must leave a usable input context.")
        if self.RESEARCH_MAP_OUTPUT_TOKENS > self.LLM_OUTPUT_TOKENS:
            raise ValueError("Map output must fit inside the reserved LLM output budget.")
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()


def validate_runtime_settings(config: Settings = settings) -> None:
    """Fail startup for unsafe production defaults and malformed log settings."""

    import logging

    if not isinstance(logging.getLevelName(config.LOG_LEVEL.strip().upper()), int):
        raise ValueError(f"Unsupported LOG_LEVEL: {config.LOG_LEVEL!r}")

    if config.ENVIRONMENT.strip().lower() in {"production", "prod"}:
        if config.AUTH_SIGNING_SECRET == "development-only-change-me" or len(
            config.AUTH_SIGNING_SECRET
        ) < 32:
            raise ValueError("Production requires a unique AUTH_SIGNING_SECRET of at least 32 characters.")
        allowed_origins = [origin.strip() for origin in config.CORS_ORIGINS.split(",")]
        if "*" in allowed_origins:
            raise ValueError("Wildcard CORS origins are not allowed in production.")
        if config.ENABLE_UNSANDBOXED_PYTHON_EXECUTION:
            raise ValueError(
                "The unisolated Python executor cannot be enabled in production."
            )
