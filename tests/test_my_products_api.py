"""My products API: owner scoping, conflicts, validation and default rules, over an in-memory repository."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.dependencies import get_current_user_id, get_my_product_repository  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.competitive_intelligence.models import MyProduct  # noqa: E402
from app.shared.errors import ConflictError, ResourceNotFoundError  # noqa: E402


class FakeRepo:
    """Mirrors the Postgres repository rules: first is default, delete promotes oldest, names unique."""

    def __init__(self) -> None:
        self.rows: dict[str, list[MyProduct]] = {}

    def _owned(self, owner: str, product_id: str) -> MyProduct:
        for row in self.rows.get(owner, []):
            if str(row.id) == product_id:
                return row
        raise ResourceNotFoundError("Product was not found.", entity="my_product")

    async def list(self, owner: str) -> list[MyProduct]:
        return list(self.rows.get(owner, []))

    async def create(self, owner: str, data: MyProduct) -> MyProduct:
        rows = self.rows.setdefault(owner, [])
        if any(r.name.lower() == data.name.lower() for r in rows):
            raise ConflictError("You already have a product with this name.")
        now = datetime.now(timezone.utc)
        row = data.model_copy(update={"id": uuid4(), "is_default": not rows, "created_at": now, "updated_at": now})
        rows.append(row)
        return row

    async def update(self, owner: str, product_id: str, data: MyProduct) -> MyProduct:
        old = self._owned(owner, product_id)
        row = data.model_copy(update={"id": old.id, "is_default": old.is_default, "created_at": old.created_at})
        self.rows[owner][self.rows[owner].index(old)] = row
        return row

    async def set_default(self, owner: str, product_id: str) -> MyProduct:
        target = self._owned(owner, product_id)
        self.rows[owner] = [r.model_copy(update={"is_default": r.id == target.id}) for r in self.rows[owner]]
        return self._owned(owner, product_id)

    async def delete(self, owner: str, product_id: str) -> None:
        target = self._owned(owner, product_id)
        self.rows[owner].remove(target)
        if target.is_default and self.rows[owner]:
            self.rows[owner][0] = self.rows[owner][0].model_copy(update={"is_default": True})


class TestMyProductsApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repo = FakeRepo()
        self.owner = "alice"
        app.dependency_overrides[get_current_user_id] = lambda: self.owner
        app.dependency_overrides[get_my_product_repository] = lambda: self.repo
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def _create(self, name: str, **extra) -> dict:
        response = await self.client.post("/api/v1/my-products", json={"name": name, **extra})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_first_product_is_default_second_is_not(self) -> None:
        first = await self._create("CafePOS", market="vn", website="https://cafepos.example.com")
        second = await self._create("Notes")
        self.assertTrue(first["is_default"])
        self.assertFalse(second["is_default"])
        self.assertEqual(first["market"], "vn")
        listed = (await self.client.get("/api/v1/my-products")).json()
        self.assertEqual([p["name"] for p in listed], ["CafePOS", "Notes"])

    async def test_duplicate_name_conflicts(self) -> None:
        await self._create("CafePOS")
        response = await self.client.post("/api/v1/my-products", json={"name": "cafepos"})
        self.assertEqual(response.status_code, 409)

    async def test_validation_errors(self) -> None:
        for body in ({"name": ""}, {"name": "X", "market": "eu"}, {"name": "X", "website": "ftp://x"}, {}):
            response = await self.client.post("/api/v1/my-products", json=body)
            self.assertEqual(response.status_code, 422, body)

    async def test_update(self) -> None:
        created = await self._create("CafePOS")
        response = await self.client.patch(f"/api/v1/my-products/{created['id']}",
                                           json={"name": "CafePOS 2", "description": "POS", "market": "vn",
                                                 "known_competitors": ["Square"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["name"], body["market"], body["known_competitors"]),
                         ("CafePOS 2", "vn", ["Square"]))
        self.assertTrue(body["is_default"])

    async def test_set_default_and_delete_promotes(self) -> None:
        first = await self._create("A")
        second = await self._create("B")
        response = await self.client.post(f"/api/v1/my-products/{second['id']}/default")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_default"])
        listed = {p["name"]: p["is_default"] for p in (await self.client.get("/api/v1/my-products")).json()}
        self.assertEqual(listed, {"A": False, "B": True})
        self.assertEqual((await self.client.delete(f"/api/v1/my-products/{second['id']}")).status_code, 204)
        listed = (await self.client.get("/api/v1/my-products")).json()
        self.assertEqual([(p["id"], p["is_default"]) for p in listed], [(first["id"], True)])

    async def test_other_owner_gets_404(self) -> None:
        created = await self._create("Mine")
        self.owner = "bob"
        url = f"/api/v1/my-products/{created['id']}"
        self.assertEqual((await self.client.patch(url, json={"name": "Hacked"})).status_code, 404)
        self.assertEqual((await self.client.post(f"{url}/default")).status_code, 404)
        self.assertEqual((await self.client.delete(url)).status_code, 404)
        self.assertEqual((await self.client.get("/api/v1/my-products")).json(), [])
        self.assertEqual((await self.client.delete(f"/api/v1/my-products/{uuid4()}")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
