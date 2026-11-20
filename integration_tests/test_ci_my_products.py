"""Own-product storage rules on a disposable PostgreSQL target only."""

import asyncio
import unittest
from uuid import uuid4

from integration_tests.environment import require_integration_environment

require_integration_environment()

from sqlalchemy import text

from app.db.init_db import init_tables, seed_defaults
from app.db.postgres_client import AsyncSessionLocal, engine
from app.infrastructure.postgres.my_product_repository import PostgresMyProductRepository
from app.modules.competitive_intelligence.models import MyProduct
from app.shared.errors import ConflictError, ResourceNotFoundError


def setUpModule() -> None:
    asyncio.run(init_tables())
    asyncio.run(seed_defaults())


class TestMyProductRepository(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = PostgresMyProductRepository()
        self.owner, self.other = str(uuid4()), str(uuid4())
        async with AsyncSessionLocal() as session:
            for user_id in (self.owner, self.other):
                await session.execute(text(
                    "INSERT INTO users (id, email, password_hash, display_name, is_active) "
                    "VALUES (:id, :email, 'x', 'Tester', true)"), {"id": user_id, "email": f"{user_id}@example.test"})
            await session.commit()
        self.addAsyncCleanup(self.cleanup)

    async def cleanup(self) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": self.owner, "b": self.other})
            await session.commit()
        await engine.dispose()

    async def test_default_rules_isolation_and_cascade(self) -> None:
        first = await self.repository.create(self.owner, MyProduct(name="Cafe POS", market="vn"))
        second = await self.repository.create(self.owner, MyProduct(name="Notes App"))
        self.assertTrue(first.is_default)
        self.assertFalse(second.is_default)

        swapped = await self.repository.set_default(self.owner, str(second.id))
        self.assertTrue(swapped.is_default)
        self.assertFalse((await self.repository.get(self.owner, str(first.id))).is_default)

        third = await self.repository.create(self.owner, MyProduct(name="Third"))
        await self.repository.delete(self.owner, str(second.id))
        remaining = {p.name: p.is_default for p in await self.repository.list(self.owner)}
        self.assertEqual(remaining, {"Cafe POS": True, "Third": False})

        found = await self.repository.find_by_name(self.owner, "  cafe pos ")
        self.assertEqual(found.id, first.id)
        with self.assertRaises(ConflictError):
            await self.repository.create(self.owner, MyProduct(name="CAFE POS"))

        self.assertEqual(await self.repository.list(self.other), [])
        self.assertIsNone(await self.repository.find_by_name(self.other, "Cafe POS"))
        with self.assertRaises(ResourceNotFoundError):
            await self.repository.get(self.other, str(first.id))
        with self.assertRaises(ResourceNotFoundError):
            await self.repository.delete(self.other, str(third.id))

        async with AsyncSessionLocal() as session:
            await session.execute(text("DELETE FROM users WHERE id=:id"), {"id": self.owner})
            await session.commit()
        self.assertEqual(await self.repository.list(self.owner), [])


if __name__ == "__main__":
    unittest.main()
