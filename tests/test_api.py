import os
import sys
import unittest
from typing import Any
from unittest.mock import patch

import httpx

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.main import app
from app.api.dependencies import get_run_query_service, get_workflow_service
from app.execution.state import SupervisorOutput, Task
from app.core.config import settings
from app.modules.identity.security import create_access_token


class _FakeStructuredOutput:
    async def ainvoke(self, messages: Any) -> SupervisorOutput:
        return SupervisorOutput(
            decision="propose_plan",
            mode="conversation",
            assistant_message="Drafted a plan for review.",
            plan=[
                Task(
                    id=1,
                    node="source_researcher",
                    status="pending",
                    description="Find primary sources about local LLMs",
                )
            ],
        )


class _FakeLLM:
    def with_structured_output(self, schema: type[SupervisorOutput]) -> _FakeStructuredOutput:
        return _FakeStructuredOutput()

class TestAPIEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ["TESTING"] = "true"
        self.transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test", follow_redirects=True)


    async def asyncTearDown(self):
        await self.client.aclose()
        from app.db.mongo_client import close_mongo_connection
        from app.db.redis_client import close_redis_connection
        await close_mongo_connection()
        await close_redis_connection()

    async def test_root_endpoint(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("message", response.json())

    async def test_health_endpoint(self):
        response = await self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertIn("databases", data)

    async def test_catalog_tools_endpoint(self):
        response = await self.client.get("/api/v1/catalog/tools")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    async def test_collection_routes_require_the_canonical_no_slash_path(self):
        class FakeRunService:
            async def list_runs(self, flow_id=None, limit=50, user_id=None):
                return []

        class FakeWorkflowService:
            async def list_workflows(self, user_id=None):
                return []

        previous_run_override = app.dependency_overrides.get(get_run_query_service)
        previous_workflow_override = app.dependency_overrides.get(get_workflow_service)
        app.dependency_overrides[get_run_query_service] = FakeRunService
        app.dependency_overrides[get_workflow_service] = FakeWorkflowService
        token = create_access_token("api-user", settings.AUTH_SIGNING_SECRET, 300)
        try:
            with patch.dict(os.environ, {"TESTING": "false"}):
                canonical_runs = await self.client.get(
                    "/api/v1/runs",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                )
                noncanonical_runs = await self.client.get(
                    "/api/v1/runs/",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                )
                canonical_flows = await self.client.get(
                    "/api/v1/flows",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                )
                noncanonical_flows = await self.client.get(
                    "/api/v1/flows/",
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                )
                self.assertEqual(canonical_runs.status_code, 200)
                self.assertEqual(canonical_flows.status_code, 200)
                self.assertEqual(noncanonical_runs.status_code, 404)
                self.assertEqual(noncanonical_flows.status_code, 404)
                self.assertEqual(canonical_runs.history, [])
                self.assertEqual(canonical_flows.history, [])
        finally:
            if previous_run_override is None:
                app.dependency_overrides.pop(get_run_query_service, None)
            else:
                app.dependency_overrides[get_run_query_service] = previous_run_override
            if previous_workflow_override is None:
                app.dependency_overrides.pop(get_workflow_service, None)
            else:
                app.dependency_overrides[get_workflow_service] = previous_workflow_override

    @patch("app.execution.llm.get_llm", return_value=_FakeLLM())
    async def test_conversation_build_endpoint(self, mock_get_llm: Any) -> None:
        create_response = await self.client.post(
            "/api/v1/conversations",
            json={"title": "Technology research"},
        )
        self.assertEqual(create_response.status_code, 201)
        conversation_id = create_response.json()["id"]

        message_response = await self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Research local LLMs", "model_name": "gemma2:latest"},
        )
        self.assertEqual(message_response.status_code, 200)
        self.assertEqual(message_response.json()["status"], "waiting_for_user")
        self.assertEqual(message_response.json()["metadata"]["model_name"], "gemma2:latest")
        self.assertTrue(message_response.json()["metadata"]["use_llm"])
        mock_get_llm.assert_called_once_with(model_name="gemma2:latest", temperature=0.2)

        messages_response = await self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(messages_response.json()[0]["role"], "user")

    @patch("app.execution.llm.get_llm", return_value=_FakeLLM())
    async def test_delete_conversation_removes_owned_history(self, mock_get_llm: Any) -> None:
        create_response = await self.client.post(
            "/api/v1/conversations",
            json={"title": "Conversation to delete"},
        )
        self.assertEqual(create_response.status_code, 201)
        conversation_id = create_response.json()["id"]

        message_response = await self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Research local LLMs"},
        )
        self.assertEqual(message_response.status_code, 200)

        delete_response = await self.client.delete(
            f"/api/v1/conversations/{conversation_id}"
        )
        self.assertEqual(delete_response.status_code, 204)

        detail_response = await self.client.get(
            f"/api/v1/conversations/{conversation_id}"
        )
        messages_response = await self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(messages_response.status_code, 404)

        list_response = await self.client.get("/api/v1/conversations")
        self.assertNotIn(
            conversation_id,
            [conversation["id"] for conversation in list_response.json()],
        )
        mock_get_llm.assert_called_once()

    async def test_workflow_update_creates_version_and_archive_preserves_history(self):
        create_response = await self.client.post(
            "/api/v1/flows",
            json={
                "name": "Versioned research",
                "definition": {
                    "flow_id": "versioned-research",
                    "name": "Versioned research",
                    "tasks": [
                        {
                            "id": 1,
                            "node": "web_search",
                            "status": "pending",
                            "description": "Search sources",
                            "dependencies": [],
                        }
                    ],
                },
            },
        )
        self.assertEqual(create_response.status_code, 201)
        workflow = create_response.json()
        self.assertEqual(workflow["version_number"], 1)
        self.assertEqual(workflow["status"], "active")

        update_response = await self.client.put(
            f"/api/v1/flows/{workflow['id']}",
            json={"name": "Versioned technology research"},
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()
        self.assertEqual(updated["version_number"], 2)
        self.assertNotEqual(updated["version_id"], workflow["version_id"])

        archive_response = await self.client.delete(
            f"/api/v1/flows/{workflow['id']}"
        )
        self.assertEqual(archive_response.status_code, 200)
        self.assertEqual(archive_response.json()["status"], "archived")

        run_response = await self.client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            json={},
        )
        self.assertEqual(run_response.status_code, 404)

if __name__ == "__main__":
    unittest.main()
