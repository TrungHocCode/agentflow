import uuid
import json
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.mongo_client import get_mongo_db
from app.modules.flows.models import FlowModel
from app.modules.runs.models import RunDocument, Task
from app.execution.nodes.dispatcher import TaskDispatcher
from app.execution.state import State

# In-memory store fallback for environments without live MongoDB
_IN_MEMORY_RUNS: Dict[str, Dict[str, Any]] = {}


class RunService:
    @staticmethod
    async def save_run_doc(doc: RunDocument) -> None:
        doc_dict = doc.model_dump()
        _IN_MEMORY_RUNS[doc.run_id] = doc_dict
        try:
            db = get_mongo_db()
            if db is not None:
                await db.runs.replace_one({"run_id": doc.run_id}, doc_dict, upsert=True)
        except Exception:
            # Fallback silently to in-memory if DB connection error
            pass

    @staticmethod
    async def get_run(run_id: str) -> Optional[RunDocument]:
        try:
            db = get_mongo_db()
            if db is not None:
                data = await db.runs.find_one({"run_id": run_id})
                if data:
                    if "_id" in data:
                        del data["_id"]
                    return RunDocument(**data)
        except Exception:
            pass

        if run_id in _IN_MEMORY_RUNS:
            return RunDocument(**_IN_MEMORY_RUNS[run_id])
        return None

    @staticmethod
    async def list_runs(flow_id: Optional[str] = None, limit: int = 50) -> List[RunDocument]:
        runs: List[RunDocument] = []
        try:
            db = get_mongo_db()
            if db is not None:
                query = {"flow_id": flow_id} if flow_id else {}
                cursor = db.runs.find(query).limit(limit)
                async for data in cursor:
                    if "_id" in data:
                        del data["_id"]
                    runs.append(RunDocument(**data))
                if runs:
                    return runs
        except Exception:
            pass

        for doc_dict in _IN_MEMORY_RUNS.values():
            if flow_id is None or doc_dict.get("flow_id") == flow_id:
                runs.append(RunDocument(**doc_dict))
            if len(runs) >= limit:
                break
        return runs

    @staticmethod
    async def create_run(
        flow_id: str,
        input_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        db_session: Optional[AsyncSession] = None
    ) -> RunDocument:
        run_id = str(uuid.uuid4())
        plan: List[Task] = []

        # If flow_id exists in Postgres, extract its definition
        if db_session:
            try:
                result = await db_session.execute(select(FlowModel).where(FlowModel.id == flow_id))
                flow_record = result.scalar_one_or_none()
                if flow_record and flow_record.definition:
                    raw_tasks = flow_record.definition.get("tasks", [])
                    for t in raw_tasks:
                        if isinstance(t, dict):
                            plan.append(Task(**t))
                        elif isinstance(t, Task):
                            plan.append(t)
            except Exception:
                # Fallback if DB is offline or unreachable in test environment
                pass


        logs = [f"[RunService] Initialized Run {run_id} for Flow {flow_id}."]
        if input_message:
            logs.append(f"[User Input]: {input_message}")

        doc = RunDocument(
            run_id=run_id,
            flow_id=flow_id,
            status="pending",
            mode="executing",
            plan=plan,
            logs=logs,
            metadata=metadata or {}
        )
        await RunService.save_run_doc(doc)
        return doc

    @staticmethod
    async def execute_step(run_id: str) -> Optional[RunDocument]:
        run_doc = await RunService.get_run(run_id)
        if not run_doc:
            return None

        if run_doc.status in ("completed", "failed"):
            return run_doc

        run_doc.status = "running"
        await RunService.save_run_doc(run_doc)

        dispatcher = TaskDispatcher()
        state: State = {
            "messages": run_doc.logs,
            "plan": run_doc.plan,
            "current_task": run_doc.current_task,
            "logs": [],
            "result_storage": run_doc.result_storage,
            "mode": run_doc.mode,
            "metadata": run_doc.metadata
        }

        dispatch_result = await dispatcher.dispatch(state)

        # Merge state updates
        if "plan" in dispatch_result and dispatch_result["plan"]:
            # Upsert plan tasks by ID
            updated_tasks = {t.id: t for t in dispatch_result["plan"]}
            new_plan = []
            for t in run_doc.plan:
                if t.id in updated_tasks:
                    new_plan.append(updated_tasks[t.id])
                else:
                    new_plan.append(t)
            run_doc.plan = new_plan

        if "current_task" in dispatch_result:
            run_doc.current_task = dispatch_result["current_task"]

        if "logs" in dispatch_result:
            run_doc.logs.extend(dispatch_result["logs"])

        if "mode" in dispatch_result:
            run_doc.mode = dispatch_result["mode"]

        # Check execution completion
        all_finished = len(run_doc.plan) > 0 and all(t.status in ("done", "failed", "skipped") for t in run_doc.plan)
        if all_finished or (run_doc.plan == [] and dispatch_result.get("current_task") is None):
            run_doc.status = "completed"
            run_doc.logs.append(f"[RunService] Run {run_id} completed successfully.")

        run_doc.updated_at = datetime.utcnow()
        await RunService.save_run_doc(run_doc)
        return run_doc

    @staticmethod
    async def approve_run(run_id: str, approved: bool = True, feedback: Optional[str] = None) -> Optional[RunDocument]:
        run_doc = await RunService.get_run(run_id)
        if not run_doc:
            return None

        if approved:
            run_doc.logs.append(f"[User Approval]: Plan approved. Feedback: {feedback or 'None'}")
            run_doc.status = "running"
        else:
            run_doc.logs.append(f"[User Approval]: Plan rejected. Feedback: {feedback or 'None'}")
            run_doc.status = "failed"

        run_doc.updated_at = datetime.utcnow()
        await RunService.save_run_doc(run_doc)
        return run_doc

    @staticmethod
    async def stream_run_events(run_id: str) -> AsyncGenerator[str, None]:
        run_doc = await RunService.get_run(run_id)
        if not run_doc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Run {run_id} not found'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'run_id': run_id, 'status': run_doc.status})}\n\n"

        # If run is pending/running, trigger execution steps
        max_steps = 10
        step_count = 0
        while run_doc and run_doc.status in ("pending", "running") and step_count < max_steps:
            step_count += 1
            prev_log_count = len(run_doc.logs)
            run_doc = await RunService.execute_step(run_id)
            if run_doc:
                # Yield new logs as SSE events
                new_logs = run_doc.logs[prev_log_count:]
                for log in new_logs:
                    yield f"data: {json.dumps({'type': 'log', 'message': str(log)})}\n\n"
                
                yield f"data: {json.dumps({'type': 'status_update', 'status': run_doc.status, 'current_task': run_doc.current_task.model_dump() if run_doc.current_task else None})}\n\n"
            await asyncio.sleep(0.1)

        final_doc = await RunService.get_run(run_id)
        final_status = final_doc.status if final_doc else "unknown"
        yield f"data: {json.dumps({'type': 'completed', 'run_id': run_id, 'status': final_status})}\n\n"
