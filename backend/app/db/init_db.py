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
        # Add missing defaults individually so existing, partially seeded catalogs evolve safely.
        default_tools = [
            ToolCatalogModel(
                name="web_search",
                description="Search the web and return ranked, cited results",
                config_schema={},
            ),
            ToolCatalogModel(
                name="web_search_batch",
                description="Search complementary queries concurrently and deduplicate cited results",
                config_schema={},
            ),
            ToolCatalogModel(
                name="news_crawler",
                description="Extract readable article or listing content from a URL",
                config_schema={},
            ),
            ToolCatalogModel(
                name="news_crawler_batch",
                description="Crawl selected source URLs concurrently with per-source extraction status",
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
        tool_rows_result = await session.execute(select(ToolCatalogModel))
        existing_tool_names = {tool.name for tool in tool_rows_result.scalars().all()}
        missing_tools = [tool for tool in default_tools if tool.name not in existing_tool_names]
        if missing_tools:
            print(
                "[Init DB] Seeding missing tools: "
                f"{', '.join(tool.name for tool in missing_tools)}"
            )
            session.add_all(missing_tools)

        # Seed default agent profiles idempotently so existing databases also
        # receive the role-specific profiles used by execution runtime.
        default_agents = [
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
            AgentCatalogModel(
                name="source_researcher",
                description="Collects web sources and extracts source content.",
                system_prompt=(
                    "You collect reliable primary sources for technology research. "
                    "Preserve URLs and distinguish source content from interpretation."
                ),
                tool_names=[
                    "web_search",
                    "web_search_batch",
                    "news_crawler",
                    "news_crawler_batch",
                    "http_request",
                ],
            ),
            AgentCatalogModel(
                name="synthesis_agent",
                description="Synthesizes source material into supported findings.",
                system_prompt=(
                    "You synthesize earlier research outputs into concise findings "
                    "without inventing evidence."
                ),
                tool_names=["text_summarizer", "file_reader"],
            ),
            AgentCatalogModel(
                name="report_agent",
                description="Produces structured Markdown research reports.",
                system_prompt=(
                    "You are AgentFlow's evidence-grounded research analyst and report writer. "
                    "Turn validated research inputs into a useful, analytical Markdown report."
                ),
                tool_names=["markdown_report_generator", "python_executor", "file_writer"],
            ),
            AgentCatalogModel(
                name="chart_agent",
                description="Creates chart specifications from structured research data.",
                system_prompt=(
                    "You turn structured research data into accurate and readable "
                    "chart specifications."
                ),
                tool_names=["python_executor", "file_reader", "file_writer"],
            ),
        ]
        existing_agents_result = await session.execute(select(AgentCatalogModel))
        existing_agents = {agent.name: agent for agent in existing_agents_result.scalars().all()}
        existing_names = set(existing_agents)
        missing_agents = [
            agent for agent in default_agents if agent.name not in existing_names
        ]
        if missing_agents:
            print(
                "[Init DB] Seeding missing agent profiles: "
                f"{', '.join(agent.name for agent in missing_agents)}"
            )
            session.add_all(missing_agents)

        # Grant only the newly introduced capabilities to the built-in role. Preserve
        # custom prompts and any user-configured tool names on existing profiles.
        source_profile = existing_agents.get("source_researcher")
        if source_profile is not None:
            current_tool_names = list(source_profile.tool_names or [])
            updated_tool_names = list(current_tool_names)
            for tool_name in ("web_search_batch", "news_crawler_batch"):
                if tool_name not in updated_tool_names:
                    updated_tool_names.append(tool_name)
            if updated_tool_names != current_tool_names:
                source_profile.tool_names = updated_tool_names
                print("[Init DB] Added batch research tools to source_researcher.")

        await session.commit()
        print("[Init DB] Default seeding complete.")


async def main() -> None:
    await init_tables()
    await seed_defaults()

if __name__ == "__main__":
    asyncio.run(main())
