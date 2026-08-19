import os
import sys
import unittest
import httpx

# Adjust path to import backend app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.main import app


class TestRunsAPIEndpoints(unittest.IsolatedAsyncioTestCase):
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

    async def test_start_run_and_get_details(self):
        # 1. Start run
        payload = {
            "flow_id": "test-flow-123",
            "input_message": "Hello, start execution flow!",
            "metadata": {"source": "unit_test"}
        }
        response = await self.client.post("/api/v1/runs/start", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("run_id", data)
        self.assertEqual(data["flow_id"], "test-flow-123")
        self.assertEqual(data["status"], "pending")

        run_id = data["run_id"]

        # 2. Get run details
        get_res = await self.client.get(f"/api/v1/runs/{run_id}")
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()
        self.assertEqual(get_data["run_id"], run_id)
        self.assertEqual(get_data["flow_id"], "test-flow-123")

    async def test_get_nonexistent_run(self):
        response = await self.client.get("/api/v1/runs/nonexistent-id-999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())

    async def test_list_runs(self):
        # Start a run first
        await self.client.post("/api/v1/runs/start", json={"flow_id": "list-flow-456"})

        response = await self.client.get("/api/v1/runs?flow_id=list-flow-456")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) >= 1)
        self.assertEqual(data[0]["flow_id"], "list-flow-456")

    async def test_approve_run(self):
        # Start a run
        start_res = await self.client.post("/api/v1/runs/start", json={"flow_id": "approve-flow-789"})
        run_id = start_res.json()["run_id"]

        # Approve run
        approve_res = await self.client.post(
            f"/api/v1/runs/{run_id}/approve",
            json={"approved": True, "feedback": "Looks good to go!"}
        )
        self.assertEqual(approve_res.status_code, 200)
        approve_data = approve_res.json()
        self.assertEqual(approve_data["status"], "running")

    async def test_stream_run_events(self):
        # Start a run
        start_res = await self.client.post("/api/v1/runs/start", json={"flow_id": "stream-flow-000"})
        run_id = start_res.json()["run_id"]

        # Stream events
        stream_res = await self.client.get(f"/api/v1/runs/{run_id}/stream")
        self.assertEqual(stream_res.status_code, 200)
        self.assertTrue("text/event-stream" in stream_res.headers.get("content-type", ""))
        content = stream_res.text
        self.assertIn("data:", content)
        self.assertIn("completed", content)


if __name__ == "__main__":
    unittest.main()
