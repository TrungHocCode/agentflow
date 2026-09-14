import asyncio
import os
import sys

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import Base
from app.db.postgres_client import engine, AsyncSessionLocal
from app.modules.flows.models import FlowModel
from app.modules.agent_catalog.models import AgentCatalogModel, ToolCatalogModel
from app.modules.runs.orm import RunModel, RunEventModel
from app.modules.conversations.orm import ConversationModel, ConversationMessageModel
from app.modules.workflows.orm import WorkflowVersionModel

async def init_tables():
    async with engine.begin() as conn:
        print("[Init DB] Creating PostgreSQL tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("[Init DB] Tables created successfully.")

async def seed_defaults():
    async with AsyncSessionLocal() as session:
        # Seed default tools if empty
        result = await session.execute(Base.metadata.tables["tool_catalog"].select())
        if not result.fetchall():
            print("[Init DB] Seeding default tools...")
            tools = [
                ToolCatalogModel(name="web_search", description="Search web via Brave/DuckDuckGo", config_schema={}),
                ToolCatalogModel(name="file_reader", description="Read sandboxed workspace files", config_schema={}),
                ToolCatalogModel(name="python_executor", description="Execute sandboxed Python scripts", config_schema={}),
                ToolCatalogModel(name="database_query", description="Execute SQL database queries", config_schema={}),
                ToolCatalogModel(name="http_request", description="Make HTTP GET/POST requests", config_schema={}),
                ToolCatalogModel(name="email_sender", description="Send outbound email notifications", config_schema={})
            ]
            session.add_all(tools)

        # Seed default agents if empty
        result = await session.execute(Base.metadata.tables["agent_catalog"].select())
        if not result.fetchall():
            print("[Init DB] Seeding default agents...")
            agents = [
                AgentCatalogModel(name="supervisor", description="Supervisor Agent for intent planning", system_prompt="You are a supervisor.", tool_names=[]),
                AgentCatalogModel(name="worker", description="Generic Worker Agent", system_prompt="You execute assigned tasks.", tool_names=["web_search", "file_reader"])
            ]
            session.add_all(agents)

        await session.commit()
        print("[Init DB] Default seeding complete.")

async def main():
    await init_tables()
    await seed_defaults()

if __name__ == "__main__":
    asyncio.run(main())
