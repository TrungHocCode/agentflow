import os
import sys
import unittest

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.main import app


class TestWorkflowVersionAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["TESTING"] = "true"
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_version_lifecycle(self) -> None:
        response = await self.client.post(
            "/api/v1/workflows",
            json={
                "name": "Version API research",
                "definition": {
                    "steps": [
                        {
                            "task_key": "collect",
                            "name": "Collect sources",
                            "description": "Collect technology sources",
                            "dependencies": [],
                            "expected_output_type": "raw_data",
                        }
                    ]
                },
            },
        )
        self.assertEqual(response.status_code, 201)
        workflow = response.json()

        versions = await self.client.get(f"/api/v1/workflows/{workflow['id']}/versions")
        self.assertEqual(versions.status_code, 200)
        self.assertEqual(len(versions.json()), 1)

        created = await self.client.post(
            f"/api/v1/workflows/{workflow['id']}/versions",
            json={
                "definition": {
                    "steps": [
                        {
                            "task_key": "collect",
                            "name": "Collect sources",
                            "description": "Collect technology sources",
                            "dependencies": [],
                            "expected_output_type": "raw_data",
                        },
                        {
                            "task_key": "summarize",
                            "name": "Summarize",
                            "description": "Summarize collected sources",
                            "dependencies": ["collect"],
                            "expected_output_type": "summary",
                        },
                    ]
                }
            },
        )
        self.assertEqual(created.status_code, 201)
        version = created.json()
        self.assertEqual(version["version_number"], 2)

        published = await self.client.post(
            f"/api/v1/workflows/{workflow['id']}/versions/{version['id']}/publish"
        )
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()["status"], "published")


if __name__ == "__main__":
    unittest.main()
