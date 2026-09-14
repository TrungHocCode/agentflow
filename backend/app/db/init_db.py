import asyncio
import os
import sys
from pathlib import Path

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.db.postgres_client import engine, AsyncSessionLocal
from app.infrastructure.postgres.models import AgentCatalogModel, ToolCatalogModel


def _upgrade_schema(connection: Connection) -> None:
    """Run Alembic on the synchronous connection supplied by SQLAlchemy."""

    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    if not config_path.exists():
        config_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    alembic_config = Config(str(config_path))
    alembic_config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    alembic_config.attributes["connection"] = connection
    command.upgrade(alembic_config, "head")


async def init_tables() -> None:
    async with engine.begin() as conn:
        print("[Init DB] Applying PostgreSQL migrations...")
        await conn.run_sync(_upgrade_schema)
        print("[Init DB] Migrations applied successfully.")


async def seed_defaults() -> None:
    async with AsyncSessionLocal() as session:
        # Seed default tools if empty
        result = await session.execute(select(ToolCatalogModel).limit(1))
        if result.scalar_one_or_none() is None:
            print("[Init DB] Seeding default tools...")
            tools = [
                ToolCatalogModel(
                    name="web_search",
                    description="Search web via Brave/DuckDuckGo",
                    config_schema={},
                ),
                ToolCatalogModel(
                    name="file_reader",
                    description="Read sandboxed workspace files",
                    config_schema={},
                ),
                ToolCatalogModel(
                    name="python_executor",
                    description="Execute sandboxed Python scripts",
                    config_schema={},
                ),
                ToolCatalogModel(
                    name="database_query",
                    description="Execute SQL database queries",
                    config_schema={},
                ),
                ToolCatalogModel(
                    name="http_request",
                    description="Make HTTP GET/POST requests",
                    config_schema={},
                ),
                ToolCatalogModel(
                    name="email_sender",
                    description="Send outbound email notifications",
                    config_schema={},
                ),
            ]
            session.add_all(tools)

        # Seed default agents if empty
        result = await session.execute(select(AgentCatalogModel).limit(1))
        if result.scalar_one_or_none() is None:
            print("[Init DB] Seeding default agents...")
            agents = [
                AgentCatalogModel(
                    name="supervisor",
                    description="Supervisor Agent for intent planning",
                    system_prompt="You are a supervisor.",
                    tool_names=[],
                ),
                AgentCatalogModel(
                    name="worker",
                    description="Generic Worker Agent",
                    system_prompt="You execute assigned tasks.",
                    tool_names=["web_search", "file_reader"],
                ),
            ]
            session.add_all(agents)

        await session.commit()
        print("[Init DB] Default seeding complete.")


async def main() -> None:
    await init_tables()
    await seed_defaults()

if __name__ == "__main__":
    asyncio.run(main())
