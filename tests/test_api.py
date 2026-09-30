import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from test_support import use_test_adapters
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import Request
from starlette.exceptions import HTTPException as StarletteHTTPException

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.main import app, http_error_handler
from app.api.dependencies import get_run_query_service, get_workflow_service
from app.db.postgres_client import get_db
from app.execution.model_router import InferencePurpose
from app.execution.agents.resolver import DEFAULT_AGENT_PROFILES
from app.execution.state import SupervisorOutput, Task
from app.core.config import settings
from app.modules.identity.security import create_access_token
from app.modules.identity.models import UserRecord


class _FakeStructuredOutput:
    async def astream(self, messages: Any) -> AsyncIterator[dict[str, Any]]:
        yield (await self.ainvoke(messages)).model_dump()

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
    def with_structured_output(self, schema: Any, **kwargs: Any) -> _FakeStructuredOutput:
        return _FakeStructuredOutput()

class TestAPIEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        use_test_adapters(self)
        # API unit tests must not resolve agent profiles from a running user database.
        self.enterContext(patch(
            "app.infrastructure.postgres.agent_profile_provider.PostgresAgentProfileProvider.get_agent",
            new=AsyncMock(side_effect=lambda identifier: DEFAULT_AGENT_PROFILES.get(identifier)),
        ))
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

    async def test_authentication_error_uses_standard_envelope_and_request_id(self):
        with patch.dict(os.environ, {"TESTING": "false"}):
            response = await self.client.get("/api/v1/runs")

        self.assertEqual(response.status_code, 401)
        error = response.json()["error"]
        self.assertEqual(error["category"], "authentication")
        self.assertEqual(error["code"], "http_401")
        self.assertEqual(error["request_id"], response.headers["x-request-id"])
        self.assertTrue(error["error_id"])
        self.assertFalse(error["retryable"])

    async def test_auth_dependency_does_not_relabel_database_outage_as_401(self):
        from app.shared.errors import PersistenceError

        class UnavailableIdentityService:
            async def current_user(self, _token):
                raise PersistenceError("Could not load user.")

        token = create_access_token("api-user", settings.AUTH_SIGNING_SECRET, 300)
        with patch.dict(os.environ, {"TESTING": "false"}), patch(
            "app.api.dependencies.build_auth_service",
            return_value=UnavailableIdentityService(),
        ):
            response = await self.client.get(
                "/api/v1/runs",
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "persistence_unavailable")

    async def test_auth_dependency_uses_request_scoped_database_session(self) -> None:
        session = object()

        async def override_get_db() -> AsyncIterator[object]:
            yield session

        class FakeAuthService:
            async def current_user(self, _token: str) -> UserRecord:
                now = datetime.now(timezone.utc)
                return UserRecord(
                    id="api-user",
                    email="api-user@example.com",
                    display_name="API User",
                    created_at=now,
                    updated_at=now,
                )

        class FakeRunService:
            async def list_runs(
                self,
                flow_id: str | None = None,
                limit: int = 50,
                user_id: str | None = None,
            ) -> list[object]:
                return []

        previous_db_override = app.dependency_overrides.get(get_db)
        previous_run_override = app.dependency_overrides.get(get_run_query_service)
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_run_query_service] = FakeRunService
        token = create_access_token("api-user", settings.AUTH_SIGNING_SECRET, 300)

        try:
            with patch.dict(os.environ, {"TESTING": "false"}), patch(
                "app.api.dependencies.build_auth_service",
                return_value=FakeAuthService(),
            ) as build_service:
                response = await self.client.get(
                    "/api/v1/runs",
                    headers={"Authorization": f"Bearer {token}"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [])
            build_service.assert_called_once_with(session)
        finally:
            if previous_db_override is None:
                app.dependency_overrides.pop(get_db, None)
            else:
                app.dependency_overrides[get_db] = previous_db_override
            if previous_run_override is None:
                app.dependency_overrides.pop(get_run_query_service, None)
            else:
                app.dependency_overrides[get_run_query_service] = previous_run_override

    async def test_http_5xx_does_not_expose_internal_detail(self):
        request = Request({
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/internal",
            "raw_path": b"/internal",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "state": {"request_id": "request-1"},
        })

        response = await http_error_handler(
            request,
            StarletteHTTPException(status_code=500, detail="database-password=private-value"),
        )

        self.assertEqual(response.status_code, 500)
        self.assertIn(
            "server error",
            response.body.decode("utf-8").lower(),
        )
        self.assertNotIn("private-value", response.body.decode("utf-8"))
        self.assertEqual(json.loads(response.body)["error"]["request_id"], "request-1")

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

        class FakeAuthService:
            async def current_user(self, _token):
                now = datetime.now(timezone.utc)
                return UserRecord(
                    id="api-user",
                    email="api-user@example.com",
                    display_name="API User",
                    created_at=now,
                    updated_at=now,
                )

        try:
            with patch.dict(os.environ, {"TESTING": "false"}), patch(
                "app.api.dependencies.build_auth_service",
                return_value=FakeAuthService(),
            ):
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
        self.assertEqual(message_response.json()["metadata"]["inference_purpose"], "planner")
        self.assertNotIn("model_name", message_response.json()["metadata"])
        self.assertTrue(message_response.json()["metadata"]["use_llm"])
        mock_get_llm.assert_called_once_with(
            purpose=InferencePurpose.PLANNER,
            temperature=0.2,
        )

        messages_response = await self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(messages_response.json()[0]["role"], "user")

    async def test_durable_turn_snapshot_and_cancel_endpoints(self) -> None:
        from app.infrastructure.container import build_conversation_service

        created = await self.client.post(
            "/api/v1/conversations",
            json={"title": "Durable turn API"},
        )
        conversation_id = created.json()["id"]
        service = build_conversation_service()
        accepted = await service.start_message(
            conversation_id,
            "Research API recovery",
            turn_id=str(uuid.uuid4()),
        )

        snapshots = await self.client.get(f"/api/v1/conversations/{conversation_id}/turns")
        self.assertEqual(snapshots.status_code, 200)
        self.assertEqual(snapshots.json()[0]["status"], "queued")
        self.assertNotIn("input_fingerprint", snapshots.json()[0])

        cancelled = await self.client.post(
            f"/api/v1/conversations/{conversation_id}/turns/{accepted['turn_id']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertNotIn("worker_id", cancelled.json())

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
            json={"workflow_version_id": workflow["version_id"]},
        )
        self.assertEqual(run_response.status_code, 404)

if __name__ == "__main__":
    unittest.main()
