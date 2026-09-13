"""Persistence adapter for the current run document store.

The repository keeps MongoDB and the test-only in-memory fallback behind one
interface. Replacing this adapter with PostgreSQL later should not change the
Runs application service.
"""

from typing import Any, Dict, List

from app.db.mongo_client import get_mongo_db
from app.modules.runs.models import RunDocument
from app.modules.runs.ports import RunRepository


_IN_MEMORY_RUNS: Dict[str, Dict[str, Any]] = {}


class MongoRunRepository(RunRepository):
    """Store RunDocument records in MongoDB with a test fallback."""

    async def save(self, document: RunDocument) -> None:
        document_dict = document.model_dump()
        _IN_MEMORY_RUNS[document.run_id] = document_dict
        try:
            database = get_mongo_db()
            if database is not None:
                await database.runs.replace_one(
                    {"run_id": document.run_id},
                    document_dict,
                    upsert=True,
                )
        except Exception:
            # The fallback is intentionally retained for the current test-only path.
            pass

    async def get(self, run_id: str) -> RunDocument | None:
        try:
            database = get_mongo_db()
            if database is not None:
                data = await database.runs.find_one({"run_id": run_id})
                if data:
                    data.pop("_id", None)
                    return RunDocument(**data)
        except Exception:
            pass

        data = _IN_MEMORY_RUNS.get(run_id)
        return RunDocument(**data) if data else None

    async def list(self, flow_id: str | None = None, limit: int = 50) -> List[RunDocument]:
        runs: List[RunDocument] = []
        try:
            database = get_mongo_db()
            if database is not None:
                query = {"flow_id": flow_id} if flow_id else {}
                cursor = database.runs.find(query).limit(limit)
                async for data in cursor:
                    data.pop("_id", None)
                    runs.append(RunDocument(**data))
                if runs:
                    return runs
        except Exception:
            pass

        for data in _IN_MEMORY_RUNS.values():
            if flow_id is None or data.get("flow_id") == flow_id:
                runs.append(RunDocument(**data))
            if len(runs) >= limit:
                break
        return runs
