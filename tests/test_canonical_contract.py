import os
import sys
import unittest
from test_support import use_test_adapters

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.main import app
from app.modules.workflows.contract import (
    canonicalize_workflow_definition,
    normalize_workflow_definition,
)


class TestCanonicalWorkflowContract(unittest.TestCase):
    def test_steps_are_normalized_to_legacy_execution_tasks(self):
        normalized = normalize_workflow_definition(
            {
                "name": "Research",
                "steps": [
                    {"task_key": "search", "name": "Search", "agent_id": "web_search"},
                    {
                        "task_key": "report",
                        "name": "Report",
                        "agent_id": "reporter",
                        "dependencies": ["search"],
                    },
                ],
            }
        )
        self.assertEqual([task["id"] for task in normalized["tasks"]], [1, 2])
        self.assertEqual(normalized["tasks"][1]["dependencies"], [1])
        self.assertEqual(normalized["tasks"][1]["task_key"], "report")

    def test_canonical_step_contract_is_preserved_losslessly(self):
        step = {
            "task_key": "collect-primary",
            "name": "Collect primary sources",
            "description": "Find official technical documentation.",
            "agent_id": "source_researcher",
            "tool_ids": ["tool-uuid"],
            "dependencies": [],
            "input_mapping": {"query": "{{input.topic}}"},
            "expected_output_type": "normalized_data",
            "config": {"timeout_seconds": 90, "max_iterations": 3, "custom": {"x": 1}},
            "position": 4,
            "vendor_extension": {"keep": True},
            "status": "completed",
        }
        normalized = normalize_workflow_definition({"name": "Research", "steps": [step]})
        self.assertEqual(normalized["steps"][0]["task_key"], "collect-primary")
        self.assertEqual(normalized["steps"][0]["input_mapping"], {"query": "{{input.topic}}"})
        self.assertEqual(normalized["steps"][0]["tool_ids"], ["tool-uuid"])
        self.assertEqual(normalized["steps"][0]["vendor_extension"], {"keep": True})
        self.assertNotIn("status", normalized["steps"][0])
        self.assertEqual(normalized["tasks"][0]["expected_output_type"], "normalized_data")
        self.assertEqual(normalized["tasks"][0]["status"], "pending")

    def test_legacy_tasks_are_exposed_as_canonical_steps(self):
        canonical = canonicalize_workflow_definition(
            {
                "flow_id": "flow-1",
                "name": "Research",
                "tasks": [
                    {
                        "id": 1,
                        "node": "web_search",
                        "status": "pending",
                        "description": "Search sources",
                        "dependencies": [],
                    }
                ],
            }
        )
        self.assertEqual(canonical["steps"][0]["task_key"], "1")
        self.assertEqual(canonical["steps"][0]["description"], "Search sources")


class TestCanonicalWorkflowAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        use_test_adapters(self)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_canonical_workflow_route_accepts_steps(self):
        response = await self.client.post(
            "/api/v1/workflows",
            json={
                "name": "Canonical research",
                "definition": {
                    "steps": [
                        {
                            "task_key": "search",
                            "name": "Search sources",
                            "description": "Search sources",
                            "agent_id": "web_search",
                            "dependencies": [],
                        }
                    ]
                },
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["version_number"], 1)


if __name__ == "__main__":
    unittest.main()
