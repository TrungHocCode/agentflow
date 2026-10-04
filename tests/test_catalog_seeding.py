"""Catalog defaults stay aligned with executable tools without overwriting user records."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.db.init_db import seed_defaults
from app.execution.tools.base import ToolRegistry
from app.execution.tools.registry import autodiscover_tools
from app.infrastructure.postgres.models import AgentCatalogModel, ToolCatalogModel
from app.infrastructure.postgres.catalog_repository import PostgresCatalogRepository


class FakeResult:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list:
        return list(self.rows)


class FakeSeedSession:
    def __init__(self) -> None:
        self.tools = [ToolCatalogModel(name="web_search", description="Custom description",
                                      is_active=False, config_schema={"custom": True})]
        self.agents = [AgentCatalogModel(name="report_agent", system_prompt="Custom prompt",
                                        tool_names=["file_reader"], is_active=False)]
        self.query_count = 0

    async def __aenter__(self) -> "FakeSeedSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _statement: object) -> FakeResult:
        self.query_count += 1
        return FakeResult(self.tools if self.query_count % 2 else self.agents)

    def add_all(self, rows: list) -> None:
        for row in rows:
            (self.tools if isinstance(row, ToolCatalogModel) else self.agents).append(row)

    async def commit(self) -> None:
        return None


class CatalogSeedingTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_marks_unimplemented_tools_as_unavailable(self) -> None:
        autodiscover_tools()
        rows = [ToolCatalogModel(id=name, name=name, is_active=True, config_schema={})
                for name in ("web_search", "unimplemented_tool")]
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        with patch.dict(os.environ, {"TESTING": "false"}):
            tools = await PostgresCatalogRepository(session).list_tools()
        self.assertTrue(tools[0].is_available)
        self.assertFalse(tools[1].is_available)
        self.assertIn("runtime implementation", tools[1].unavailable_reason)

    async def test_seed_is_complete_idempotent_and_preserves_custom_records(self) -> None:
        autodiscover_tools()
        session = FakeSeedSession()
        with patch("app.db.init_db.AsyncSessionLocal", return_value=session):
            await seed_defaults()
            counts = (len(session.tools), len(session.agents))
            await seed_defaults()
        self.assertEqual(counts, (len(session.tools), len(session.agents)))
        self.assertEqual({tool.name for tool in session.tools}, set(ToolRegistry.list_tools()))
        self.assertEqual(session.tools[0].description, "Custom description")
        self.assertEqual(session.tools[0].config_schema, {"custom": True})
        self.assertFalse(session.tools[0].is_active)
        self.assertEqual(session.agents[0].system_prompt, "Custom prompt")
        self.assertEqual(session.agents[0].tool_names, ["file_reader"])
        self.assertFalse(session.agents[0].is_active)
        for tool in session.tools[1:]:
            self.assertIn("properties", tool.config_schema)
        names = {tool.name for tool in session.tools}
        self.assertTrue(all(set(agent.tool_names) <= names for agent in session.agents))
