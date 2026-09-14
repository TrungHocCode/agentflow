import os
import sys
import unittest
import httpx

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.main import app

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

    async def test_conversation_build_endpoint(self):
        create_response = await self.client.post(
            "/api/v1/conversations",
            json={"title": "Technology research"},
        )
        self.assertEqual(create_response.status_code, 201)
        conversation_id = create_response.json()["id"]

        message_response = await self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Research local LLMs"},
        )
        self.assertEqual(message_response.status_code, 200)
        self.assertEqual(message_response.json()["status"], "waiting_for_user")

        messages_response = await self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(messages_response.json()[0]["role"], "user")

if __name__ == "__main__":
    unittest.main()
