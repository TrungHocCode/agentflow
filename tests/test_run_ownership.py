"""Run event/artifact/stream reads must not leak another user's run."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from test_support import use_test_adapters

from app.api.dependencies import get_current_user_id, get_run_query_service
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.main import app
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.shared.errors import ResourceNotFoundError


class TestRunReadOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        use_test_adapters(self)
        self.repository = PostgresRunRepository()
        self.run = RunDocument(
            run_id="owned-by-user-b",
            flow_id="ownership-flow",
            user_id="user-b",
            status="running",
        )
        await self.repository.save(self.run)
        self.service = RunService(
            run_repository=self.repository,
            workflow_repository=None,
            execution_port=AsyncMock(),
        )

    async def test_other_user_gets_404_for_events_artifacts_and_stream(self) -> None:
        paths = (
            f"/api/v1/runs/{self.run.run_id}/events",
            f"/api/v1/runs/{self.run.run_id}/artifacts",
            f"/api/v1/runs/{self.run.run_id}/stream",
        )
        with patch.dict(app.dependency_overrides, {
            get_run_query_service: lambda: self.service,
            get_current_user_id: lambda: "user-a",
        }):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                for path in paths:
                    with self.subTest(path=path):
                        response = await client.get(path)
                        self.assertEqual(response.status_code, 404)

    async def test_service_read_methods_require_the_owner(self) -> None:
        with self.assertRaises(ResourceNotFoundError):
            await self.service.list_run_events(self.run.run_id, "user-a")
        with self.assertRaises(ResourceNotFoundError):
            await self.service.list_run_results(self.run.run_id, "user-a")
        with self.assertRaises(ResourceNotFoundError):
            await self.service.list_run_evidence(self.run.run_id, "user-a")
        with self.assertRaises(ResourceNotFoundError):
            await self.service.list_run_artifacts(self.run.run_id, "user-a")
        with self.assertRaises(ResourceNotFoundError):
            await self.service.get_run_artifact(self.run.run_id, "artifact-1", "user-a")
        with self.assertRaises(ResourceNotFoundError):
            await anext(self.service.stream_run_events(self.run.run_id, "user-a"))

    async def test_owner_can_still_read_events(self) -> None:
        self.assertEqual(await self.service.list_run_events(self.run.run_id, "user-b"), [])


if __name__ == "__main__":
    unittest.main()
