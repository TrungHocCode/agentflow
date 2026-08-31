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

        mode = "conversation"
        result_storage = []

        # Invoke LangGraph supervisor_node để phân tích intent và đề xuất plan.
        # Graph sẽ tự PAUSE tại interrupt_before=["dispatcher_node"] sau supervisor_node,
        # lưu state vào checkpointer với thread_id=run_id, và trả về kết quả trung gian.
        if not plan:
            try:
                from app.execution.graph import build_execution_graph, get_graph_config
                graph_app = build_execution_graph()
                init_state: State = {
                    "messages": [input_message] if input_message else [],
                    "plan": [],
                    "current_task": None,
                    "logs": logs,
                    "result_storage": [],
                    "mode": "conversation",
                    "metadata": metadata or {}
                }
                config = get_graph_config(run_id)
                res_state = await graph_app.ainvoke(init_state, config=config)
                plan = res_state.get("plan", [])
                mode = res_state.get("mode", "conversation")
                logs = res_state.get("logs", logs)
                result_storage = res_state.get("result_storage", [])
            except Exception as e:
                logs.append(f"[RunService Warning] Graph invocation error: {e}")

        doc = RunDocument(
            run_id=run_id,
            flow_id=flow_id,
            status="pending",
            mode=mode,
            plan=plan,
            logs=logs,
            result_storage=result_storage,
            metadata=metadata or {}
        )
        await RunService.save_run_doc(doc)
        return doc

    @staticmethod
    async def send_message(
        run_id: str,
        message: str,
    ) -> Optional[RunDocument]:
        """
        Gửi message follow-up vào conversation đang chờ của một run.

        Dùng cho multi-turn conversation với Supervisor (làm rõ yêu cầu).
        Graph được resume từ checkpoint với cùng thread_id và message mới,
        sau đó lại PAUSE chờ user phản hồi tiếp hoặc approve.

        Args:
            run_id: ID của run đang ở chế độ conversation
            message: Nội dung message từ user

        Returns:
            RunDocument sau khi cập nhật
        """
        run_doc = await RunService.get_run(run_id)
        if not run_doc:
            return None

        if run_doc.status in ("completed", "failed"):
            return run_doc

        try:
            from app.execution.graph import build_execution_graph, get_graph_config
            graph_app = build_execution_graph()
            config = get_graph_config(run_id)

            # Resume graph với message mới — graph tiếp tục từ checkpoint
            res_state = await graph_app.ainvoke(
                {"messages": [message]},
                config=config
            )

            plan = res_state.get("plan", run_doc.plan)
            mode = res_state.get("mode", run_doc.mode)
            new_logs = res_state.get("logs", [])

            run_doc.plan = plan
            run_doc.mode = mode
            run_doc.logs.extend(new_logs)
            run_doc.logs.append(f"[User Message]: {message}")
            run_doc.updated_at = datetime.utcnow()
        except Exception as e:
            run_doc.logs.append(f"[RunService Warning] send_message error: {e}")

        await RunService.save_run_doc(run_doc)
        return run_doc

    @staticmethod
    async def approve_run(
        run_id: str,
        approved: bool = True,
        feedback: Optional[str] = None
    ) -> Optional[RunDocument]:
        """
        Approve hoặc reject plan đề xuất của Supervisor.

        Nếu approved=True: Resume graph từ checkpoint bằng cách truyền
        state update mode='executing' vào cùng thread_id. Graph sẽ tiếp tục
        từ điểm PAUSE (trước dispatcher_node) và thực thi toàn bộ task plan.

        Nếu approved=False: Đánh dấu run là failed, không resume graph.

        Args:
            run_id: ID của run cần approve
            approved: True để approve, False để reject
            feedback: Phản hồi tùy chọn từ user

        Returns:
            RunDocument sau khi cập nhật
        """
        run_doc = await RunService.get_run(run_id)
        if not run_doc:
            return None

        if not approved:
            run_doc.logs.append(f"[User Approval]: Plan rejected. Feedback: {feedback or 'None'}")
            run_doc.status = "failed"
            run_doc.updated_at = datetime.utcnow()
            await RunService.save_run_doc(run_doc)
            return run_doc

        run_doc.logs.append(f"[User Approval]: Plan approved. Feedback: {feedback or 'None'}")
        run_doc.status = "running"
        run_doc.updated_at = datetime.utcnow()
        await RunService.save_run_doc(run_doc)

        # Resume graph từ checkpoint: truyền state update mode="executing".
        # Graph tiếp tục từ điểm PAUSE (interrupt_before=dispatcher_node),
        # chạy toàn bộ dispatcher → worker → ... → END và tự kết thúc.
        try:
            from app.execution.graph import build_execution_graph, get_graph_config
            graph_app = build_execution_graph()
            config = get_graph_config(run_id)

            res_state = await graph_app.ainvoke(
                {"mode": "executing"},
                config=config
            )

            plan = res_state.get("plan", run_doc.plan)
            result_storage = res_state.get("result_storage", run_doc.result_storage)
            new_logs = res_state.get("logs", [])

            run_doc.plan = plan
            run_doc.result_storage = result_storage
            run_doc.logs.extend(new_logs)

            all_finished = len(plan) > 0 and all(t.status in ("done", "failed", "skipped") for t in plan)
            run_doc.status = "completed" if all_finished else "running"
            run_doc.logs.append(f"[RunService] Run {run_id} execution finished. Status: {run_doc.status}")
        except Exception as e:
            run_doc.logs.append(f"[RunService Warning] approve_run graph resume error: {e}")
            run_doc.status = "failed"

        run_doc.updated_at = datetime.utcnow()
        await RunService.save_run_doc(run_doc)
        return run_doc

    @staticmethod
    async def stream_run_events(run_id: str) -> AsyncGenerator[str, None]:
        """
        Stream live execution logs và progress updates qua Server-Sent Events (SSE).

        Dùng graph.astream_events() để nhận real-time events từ LangGraph execution,
        thay vì polling loop thủ công. Graph phải đã được resume (approve_run đã được gọi)
        hoặc đang ở trạng thái running.

        Nếu run đang pending (chờ approve), stream trả về current state mà không thực thi.
        """
        run_doc = await RunService.get_run(run_id)
        if not run_doc:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Run {run_id} not found'})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'run_id': run_id, 'status': run_doc.status})}\n\n"

        # Nếu run đang pending (chờ user approve), chỉ trả về current plan và dừng
        if run_doc.status == "pending":
            plan_data = [t.model_dump() for t in run_doc.plan]
            yield f"data: {json.dumps({'type': 'plan_ready', 'plan': plan_data, 'status': 'pending'})}\n\n"
            yield f"data: {json.dumps({'type': 'completed', 'run_id': run_id, 'status': 'pending'})}\n\n"
            return

        # Nếu run đã completed/failed, trả về logs và kết quả hiện có
        if run_doc.status in ("completed", "failed"):
            for log in run_doc.logs:
                yield f"data: {json.dumps({'type': 'log', 'message': str(log)})}\n\n"
            yield f"data: {json.dumps({'type': 'completed', 'run_id': run_id, 'status': run_doc.status})}\n\n"
            return

        # Run đang chạy (running) — stream events từ graph qua astream_events
        try:
            from app.execution.graph import build_execution_graph, get_graph_config
            graph_app = build_execution_graph()
            config = get_graph_config(run_id)

            # Với checkpointer, graph có thể tiếp tục từ checkpoint đã lưu.
            # Truyền None để resume từ state hiện tại trong checkpoint.
            async for event in graph_app.astream_events(None, config=config, version="v2"):
                event_name = event.get("event", "")
                event_data = event.get("data", {})
                node_name = event.get("name", "")

                if event_name == "on_chain_start" and node_name in ("worker_node", "dispatcher_node"):
                    yield f"data: {json.dumps({'type': 'node_start', 'node': node_name})}\n\n"

                elif event_name == "on_chain_end" and node_name in ("worker_node", "dispatcher_node"):
                    output = event_data.get("output", {})
                    logs = output.get("logs", []) if isinstance(output, dict) else []
                    current_task = output.get("current_task") if isinstance(output, dict) else None
                    for log in logs:
                        yield f"data: {json.dumps({'type': 'log', 'message': str(log)})}\n\n"
                    if current_task and hasattr(current_task, "model_dump"):
                        yield f"data: {json.dumps({'type': 'task_update', 'task': current_task.model_dump()})}\n\n"
                    elif current_task and isinstance(current_task, dict):
                        yield f"data: {json.dumps({'type': 'task_update', 'task': current_task})}\n\n"

            # Sau khi stream kết thúc, cập nhật trạng thái cuối từ checkpoint state
            final_doc = await RunService.get_run(run_id)
            final_status = final_doc.status if final_doc else "completed"
            yield f"data: {json.dumps({'type': 'completed', 'run_id': run_id, 'status': final_status})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'completed', 'run_id': run_id, 'status': 'failed'})}\n\n"
