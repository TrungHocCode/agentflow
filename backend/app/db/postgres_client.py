from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.core.config import settings

engine = create_async_engine(
    settings.POSTGRES_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    poolclass=NullPool
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    """Dependency for FastAPI endpoints to get a DB session."""
    async with AsyncSessionLocal() as session:
        yield session
