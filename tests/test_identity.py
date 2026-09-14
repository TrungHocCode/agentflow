import os
import sys
import unittest
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.identity.models import LoginRequest, RegisterRequest
from app.modules.identity.security import create_access_token, decode_access_token, hash_password, verify_password
from app.modules.identity.service import IdentityService
from app.modules.identity.models import UserRecord
from app.main import app


class FakeUserRepository:
    def __init__(self):
        self.users = {}

    async def create(self, user, password_hash):
        self.users[user.id] = (user, password_hash)
        return user

    async def get_by_email(self, email):
        return next((entry for entry in self.users.values() if entry[0].email == email), None)

    async def get(self, user_id):
        entry = self.users.get(user_id)
        return entry[0] if entry else None

    async def touch_last_login(self, user_id):
        entry = self.users.get(user_id)
        if not entry:
            return None
        user = entry[0].model_copy(update={"last_login_at": datetime.now(timezone.utc)})
        self.users[user_id] = (user, entry[1])
        return user


class TestIdentitySecurity(unittest.IsolatedAsyncioTestCase):
    async def test_password_and_token_round_trip(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong", encoded))
        token = create_access_token("user-1", "secret", 60)
        self.assertEqual(decode_access_token(token, "secret")["sub"], "user-1")

    async def test_register_login_and_current_user(self):
        repository = FakeUserRepository()
        service = IdentityService(repository, "secret", 3600)
        registered = await service.register(
            RegisterRequest(
                email="user@example.com",
                password="password123",
                display_name="Researcher",
            )
        )
        logged_in = await service.login(
            LoginRequest(email="user@example.com", password="password123")
        )
        current = await service.current_user(logged_in.access_token)
        self.assertEqual(current.id, registered.user.id)
        self.assertEqual(current.email, "user@example.com")


class TestIdentityAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ["TESTING"] = "true"
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_register_login_and_me(self):
        registered = await self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "api-user@example.com",
                "password": "password123",
                "display_name": "API User",
            },
        )
        self.assertEqual(registered.status_code, 201)

        logged_in = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "api-user@example.com", "password": "password123"},
        )
        self.assertEqual(logged_in.status_code, 200)
        token = logged_in.json()["access_token"]

        current = await self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["email"], "api-user@example.com")


if __name__ == "__main__":
    unittest.main()
