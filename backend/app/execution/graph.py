import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.runtime import Runtime

from app.core.config import settings
from app.execution.context import ExecutionContext
from app.execution.research_evidence import EvidenceStore
from app.execution.ports import AssistantTokenCallback
from app.execution.state import State, Task
from app.execution.model_router import InferencePurpose, model_name_for
from app.execution.nodes.dispatcher import TaskDispatcher
from app.execution.agents.base import SupervisorAgent, WorkerAgent
from app.execution.agents.resolver import AgentResolver
from app.execution.tools.base import ToolRegistry
from app.execution.tools.contracts import is_tool_failure, parse_tool_result
from app.execution.tools.registry import autodiscover_tools
from app.execution.checkpointer import get_checkpointer
from app.shared.execution_metrics import ExecutionTiming, serialize_execution_timings


logger = logging.getLogger(__name__)

# Ensure all tools are registered
autodiscover_tools()


SUPERVISOR_SYSTEM_PROMPT = (
    "You are SupervisorAgent, an AI Agent Planner for AgentFlow platform.\n"
    "For each turn, choose exactly one decision: clarify, propose_plan, or answer.\n"
    "Choose clarify only when missing information changes the research question or deliverable materially, "
    "constraints conflict, or a required input is absent and has no safe default. Ask one concise, "
    "combined question; do not repeat information the user already supplied.\n"
    "Otherwise use sensible defaults (concise Markdown report, primary sources first, general technical "
    "audience) and state important assumptions in assistant_message.\n"
    "For an actionable workflow request, choose propose_plan and ALWAYS return at least one pending task. "
    "Return the smallest executable DAG that satisfies the request.\n"
    "Choose answer only for a direct conversational question that does not ask for a workflow.\n"
    "assistant_message is required and must not be blank: for clarify it is the question; for propose_plan "
    "it briefly explains the plan and asks for approval; for answer it is the direct response.\n"
    "Available agent roles and their currently registered, authorized tools are listed below.\n"
    "For broad research, plan source_researcher to discover candidate URLs before collecting evidence; that agent "
    "can search complementary query angles and crawl several selected URLs in bounded batches. Keep synthesis and "
    "reporting downstream of source collection, and use separate tasks only for genuinely distinct deliverables. "
    "Your responsibility is business planning: choose agent roles, task descriptions, output types, and "
    "data dependencies. The backend owns tool authorization and execution budgets; workers choose tool calls "
    "dynamically within their authorized role. Never specify tool_names, tool_ids, agent IDs, config, "
    "timeouts, or iteration limits in generated tasks. Describe source or operation constraints in "
    "the task description instead of changing permissions.\n"
    "For a comparison that names multiple subjects, preserve every subject in the source_researcher task description, "
    "along with the requested comparison dimensions and the requirement to find directly relevant primary sources "
    "for each subject. Require the researcher to report any uncovered subject explicitly. A successful tool call or "
    "HTTP 200 is not proof that research coverage is complete.\n"
    "For source research tasks, include research_requirements: subject/field pairs explicitly requested by "
    "the user, each with a stable ID. Do not invent dimensions or aliases. These are coverage expectations, "
    "not tool permissions; unmatched pairs remain unresolved, never publisher omissions.\n"
    "Use the concrete role name in each Task.node instead of the generic 'worker' whenever the role is known.\n"
    "The listed tools describe role capabilities, not fields to include in a plan. "
    "Only use the business task fields in the response schema.\n"
    "Tool names are capabilities, not agent roles.\n"
    "Every dependency must reference an existing task. Add a dependency whenever a task needs another task's "
    "output; leave independent tasks unblocked. Do not invent sources, data, or completed work.\n"
    "Use mode='conversation' for every response. The application, not the model, handles approval and execution."
)


async def supervisor_node(
    state: State,
    agent_resolver: AgentResolver | None = None,
    on_assistant_token: AssistantTokenCallback | None = None,
) -> Dict[str, Any]:
    """
    Supervisor Node handles intent analysis, multi-turn clarification, and plan formulation.
    """
    current_mode = state.get("mode", "conversation")
    plan = state.get("plan") or []

    # If already executing, maintain executing mode
    if current_mode == "executing":
        return {
            "mode": "executing",
            "logs": [f"[SupervisorNode] Already in executing mode with {len(plan)} tasks."]
        }

    metadata = state.get("metadata") or {}
    use_llm = metadata.get("use_llm", False)

    if not use_llm and os.getenv("TESTING", "").lower() != "true":
        error_id = str(uuid.uuid4())
        logger.error(
            "Refusing synthetic planning outside the isolated test environment",
            extra={"error_id": error_id, "error_code": "llm_execution_disabled"},
        )
        return {
            "mode": "conversation",
            "plan": {"__replace__": True, "tasks": []},
            "messages": ["Không thể lập kế hoạch vì dịch vụ LLM chưa được bật."],
            "logs": ["[SupervisorNode Error] LLM planning is required in normal operation."],
            "metadata": {
                **metadata,
                "planning_failed": True,
                "planning_error_id": error_id,
                "planning_error_code": "llm_execution_disabled",
                "planning_error_category": "configuration",
                "planning_error_message": "LLM planning is not enabled.",
            },
        }

    if use_llm:
        purpose = metadata.get(
            "inference_purpose",
            InferencePurpose.PLANNER.value,
        )
        model_name: str | None = None
        try:
            from app.execution.llm import get_llm
            model_name = model_name_for(purpose)
            llm = get_llm(purpose=purpose, temperature=0.2)
            resolver = agent_resolver or AgentResolver()
            tool_catalog = await resolver.format_agent_tool_catalog()
            supervisor = SupervisorAgent(
                name="supervisor",
                system_prompt=f"{SUPERVISOR_SYSTEM_PROMPT}\n{tool_catalog}",
                llm=llm
            )
            updates = await supervisor.execute(
                state,
                on_assistant_token=on_assistant_token,
            )
            decision = (updates.get("metadata") or {}).get("supervisor_decision")
            if decision == "propose_plan":
                tasks = updates.get("plan") or []
                await resolver.validate_plan(tasks)
                updates["plan"] = {"__replace__": True, "tasks": tasks}
            elif decision == "clarify":
                updates["plan"] = {"__replace__": True, "tasks": []}
            clean_metadata = dict(updates.get("metadata") or {})
            for stale_key in (
                "planning_failed",
                "planning_error_message",
                "planning_error_id",
                "planning_error_code",
                "planning_error_category",
                "llm_error",
            ):
                clean_metadata.pop(stale_key, None)
            updates["metadata"] = clean_metadata
            return updates
        except Exception as exc:
            # Never mark malformed structured output as a successful empty plan.
            error_id = str(uuid.uuid4())
            logger.error(
                "Supervisor structured planning failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "error_id": error_id,
                    "error_code": "supervisor_planning_failed",
                    "model_name": model_name,
                    "purpose": purpose,
                },
            )
            failure_update = {
                "mode": "conversation",
                "plan": {"__replace__": True, "tasks": []},
                "messages": [
                    "Tôi chưa thể tạo phản hồi hợp lệ. Vui lòng thử lại hoặc làm rõ yêu cầu."
                ],
                "logs": [
                    f"[SupervisorNode Error] Structured planning failed ({type(exc).__name__})."
                ],
                "metadata": {
                    **metadata,
                    "planning_failed": True,
                    "planning_error_id": error_id,
                    "planning_error_code": "supervisor_planning_failed",
                    "planning_error_category": "model",
                    "planning_error_message": "Supervisor could not create a valid response.",
                },
            }
            llm_call_metrics = getattr(exc, "llm_call_metrics", None)
            if llm_call_metrics:
                failure_update["llm_call_metrics"] = llm_call_metrics
            return failure_update

    user_msgs = state.get("messages") or []
    last_msg = ""
    if user_msgs:
        m = user_msgs[-1]
        last_msg = m.content.lower() if hasattr(m, "content") else str(m).lower()

    approval_keywords = ["đồng ý", "chạy đi", "ok", "yes", "run", "approve", "bắt đầu", "thực thi"]



    if any(kw in last_msg for kw in approval_keywords):
        if not plan:
            t1 = Task(id=1, node="news_crawler", status="pending", description="Crawl article from URL")
            t2 = Task(id=2, node="text_summarizer", status="pending", dependencies=[1], description="Summarize text")
            t3 = Task(id=3, node="markdown_report_generator", status="pending", dependencies=[2], description="Generate Markdown report")
            plan = [t1, t2, t3]
        return {
            "mode": "executing",
            "plan": plan,
            "metadata": {**metadata, "supervisor_decision": "propose_plan"},
            "logs": ["[SupervisorNode] User approved plan. Transitioning to 'executing'."]
        }

    if not plan:
        t1 = Task(id=1, node="news_crawler", status="pending", description="Crawl article from URL")
        t2 = Task(id=2, node="text_summarizer", status="pending", dependencies=[1], description="Summarize text")
        t3 = Task(id=3, node="markdown_report_generator", status="pending", dependencies=[2], description="Generate Markdown report")
        return {
            "mode": "conversation",
            "plan": [t1, t2, t3],
            "messages": ["Supervisor: Tôi đã lập xong kế hoạch 3 bước. Bạn có đồng ý thực thi không?"],
            "metadata": {**metadata, "supervisor_decision": "propose_plan"},
            "logs": ["[SupervisorNode] Created initial plan proposal. Awaiting user confirmation."]
        }


    return {
        "mode": "conversation",
        "logs": ["[SupervisorNode] Awaiting user approval/clarification."]
    }


async def dispatcher_node(state: State) -> Dict[str, Any]:
    """
    Dispatcher Node evaluates task dependencies and dispatches the next runnable task.
    """
    dispatcher = TaskDispatcher()
    result = await dispatcher.dispatch(state)
    return result


async def _execute_worker_node(
    state: State,
    agent_resolver: AgentResolver | None = None,
    evidence_store: EvidenceStore | None = None,
) -> Dict[str, Any]:
    """
    Worker Node executes the current dispatched task using configured tools.
    """
    current_task = state.get("current_task")
    if not current_task:
        return {"logs": ["[WorkerNode] No current_task found in state to execute."]}

    node_name = current_task.node.lower()
    tool_instances = []

    all_registered_tools = ToolRegistry.get_all_tools()

    if "crawler" in node_name or "news" in node_name or "cào" in node_name:
        tool_instances = [
            tool
            for tool in all_registered_tools
            if tool.name in {
                "news_crawler",
                "news_crawler_batch",
                "web_search",
                "web_search_batch",
                "http_request",
            }
        ]
    elif "search" in node_name or "web" in node_name or "tìm" in node_name:
        tool_instances = [
            tool
            for tool in all_registered_tools
            if tool.name in {
                "web_search",
                "web_search_batch",
                "news_crawler",
                "news_crawler_batch",
                "http_request",
            }
        ]
    elif "summarizer" in node_name or "summary" in node_name or "tóm" in node_name or "tổng" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("text_summarizer", "file_reader")]
    elif "report" in node_name or "writer" in node_name or "coder" in node_name or "markdown" in node_name or "báo cáo" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("markdown_report_generator", "python_executor", "file_writer")]
    else:
        tool_instances = all_registered_tools

    logs = [
        f"[WorkerNode] Executing Task {current_task.id} "
        f"('{current_task.description}') on node '{current_task.node}'."
    ]

    metadata = state.get("metadata") or {}
    use_llm = metadata.get("use_llm", False)

    if use_llm:
        purpose = InferencePurpose.WORKER.value
        model_name: str | None = None
        try:
            from app.execution.llm import get_llm
            resolved_config = metadata.get("resolved_model_config") or {}
            model_name = resolved_config.get("worker_model") or model_name_for(purpose)
            resolver = agent_resolver or AgentResolver()
            profiles = resolved_config.get("agent_profiles") or {}
            step_configs = resolved_config.get("steps") or {}
            step_snapshot = step_configs.get(current_task.task_key or str(current_task.id), {})
            profile_data = (
                profiles.get(current_task.agent_id)
                or profiles.get(current_task.node)
            )
            profile_override = None
            if isinstance(profile_data, dict):
                from app.execution.agents.resolver import AgentProfile

                profile_override = AgentProfile.model_validate(profile_data)
            resolved_agent = await resolver.resolve(current_task, profile_override=profile_override)
            logs.append(
                f"[WorkerNode] Resolved agent '{resolved_agent.profile.name}' "
                f"with {len(resolved_agent.tools)} authorized tools: "
                f"{', '.join(tool.name for tool in resolved_agent.tools) or 'none'}."
            )
            if resolved_agent.missing_tool_names:
                logs.append(
                    "[WorkerNode Warning] Catalog tools unavailable in runtime: "
                    f"{', '.join(resolved_agent.missing_tool_names)}."
                )
            if resolved_agent.denied_tool_names:
                logs.append(
                    "[WorkerNode Warning] Task tool override was restricted by "
                    f"agent policy: {', '.join(resolved_agent.denied_tool_names)}."
                )
            logs.append(f"[WorkerNode] Initializing live Ollama LLM ({model_name}) for ReAct loop.")
            llm = get_llm(
                purpose=InferencePurpose.WORKER,
                temperature=float(resolved_config.get("worker_temperature", 0.2)),
                model_name_override=model_name,
            )
            worker_agent = AgentResolver.create_agent(
                resolved=resolved_agent,
                llm=llm,
                task=current_task,
                system_prompt_override=step_snapshot.get("effective_system_prompt"),
            )
            agent_state = dict(state)
            worker_agent.evidence_store = evidence_store
            agent_state["metadata"] = {**metadata, "model_name": model_name}
            agent_output = await worker_agent.execute(agent_state)
            agent_output["logs"] = logs + (agent_output.get("logs") or [])
            return agent_output
        except Exception as exc:
            error_id = str(uuid.uuid4())
            logger.error(
                "Worker could not initialize its model or authorized tools",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": metadata.get("run_id"),
                    "task_execution_id": str(current_task.id),
                    "error_id": error_id,
                    "error_code": "worker_initialization_failed",
                    "model_name": model_name,
                    "purpose": purpose,
                },
            )
            safe_error = "Worker could not initialize the local model or authorized tools."
            updated_task = current_task.model_copy(update={"status": "failed", "error": safe_error})
            return {
                "plan": [updated_task],
                "current_task": updated_task,
                "metadata": {
                    **metadata,
                    "last_error": {
                        "error_id": error_id,
                        "code": "worker_initialization_failed",
                        "category": "dependency",
                        "retryable": True,
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
                "result_storage": [{
                    "task_id": current_task.id,
                    "node": current_task.node,
                    "description": current_task.description,
                    "result": "",
                    "status": "failed",
                    "error": safe_error,
                }],
                "logs": logs + ["[WorkerNode Error] Model or tool initialization failed."],
            }

    if os.getenv("TESTING", "").lower() != "true":
        error_id = str(uuid.uuid4())
        logger.error(
            "Refusing synthetic worker execution outside the isolated test environment",
            extra={
                "run_id": metadata.get("run_id"),
                "task_execution_id": str(current_task.id),
                "error_id": error_id,
                "error_code": "llm_execution_disabled",
            },
        )
        safe_error = "LLM-based worker execution is required for normal runs."
        failed_task = current_task.model_copy(update={"status": "failed", "error": safe_error})
        return {
            "plan": [failed_task],
            "current_task": failed_task,
            "metadata": {
                **metadata,
                "last_error": {
                    "error_id": error_id,
                    "code": "llm_execution_disabled",
                    "category": "configuration",
                    "retryable": False,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            "result_storage": [{
                "task_id": current_task.id,
                "node": current_task.node,
                "description": current_task.description,
                "result": "",
                "status": "failed",
                "error": safe_error,
            }],
            "logs": logs + ["[WorkerNode Error] Synthetic execution is test-only."],
        }

    result_text = ""
    status = "done"
    error_msg = None
    execution_timings: list[ExecutionTiming] = []
    timing_enabled = settings.ENABLE_EXECUTION_BENCHMARK_METRICS
    logs.append(f"[WorkerNode] Legacy execution path selected with {len(tool_instances)} tools.")

    def invoke_legacy_tool(tool: Any, args: Dict[str, Any]) -> Any:
        started_at = datetime.now(timezone.utc) if timing_enabled else None
        started = time.perf_counter() if timing_enabled else None
        try:
            result = tool.invoke(args)
        except Exception as exc:
            if timing_enabled and started_at is not None and started is not None:
                execution_timings.append(
                    ExecutionTiming(
                        operation="tool",
                        phase="execute",
                        name=tool.name,
                        agent_name=current_task.node,
                        task_id=current_task.id,
                        duration_ms=round((time.perf_counter() - started) * 1000, 3),
                        status="failed",
                        error_type=type(exc).__name__,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
            raise
        normalized_result = parse_tool_result(result, tool_name=tool.name)
        if timing_enabled and started_at is not None and started is not None:
            execution_timings.append(
                ExecutionTiming(
                    operation="tool",
                    phase="execute",
                    name=tool.name,
                    agent_name=current_task.node,
                    task_id=current_task.id,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    status="success",
                    result_status=(
                        "success"
                        if normalized_result.status == "success"
                        else "partial"
                        if normalized_result.status == "partial"
                        else "failed"
                    ),
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )
            )
        return result

    try:
        desc = current_task.description.lower()
        node = current_task.node.lower()

        if "search" in node or "tìm kiếm" in desc or "search" in desc or "tra cứu" in desc:
            search_tool = ToolRegistry.get_tool("web_search")
            if search_tool:
                # Extract clean search query from description
                query = current_task.description
                for prefix in ("tìm kiếm thông tin về", "tìm kiếm thông tin", "tìm kiếm", "search for", "search"):
                    if desc.startswith(prefix):
                        query = current_task.description[len(prefix):].strip(" :,-")
                        break
                result_text = invoke_legacy_tool(
                    search_tool,
                    {"query": query or current_task.description},
                )
            else:
                result_text = f"Executed search task: '{current_task.description}'"

        elif "crawl" in node or "crawler" in node or "crawl" in desc or "cào" in desc or "news" in desc or "http" in desc:
            crawler_tool = ToolRegistry.get_tool("news_crawler")
            if crawler_tool:
                url_match = [w for w in current_task.description.split() if w.startswith("http")]
                target_url = url_match[0] if url_match else "https://news.ycombinator.com"
                result_text = invoke_legacy_tool(crawler_tool, {"url": target_url})
            else:
                result_text = f"Executed crawler task '{current_task.description}' successfully."

        elif "summariz" in node or "summary" in node or "tóm tắt" in desc or "tổng hợp" in desc or "summariz" in desc:
            summarizer_tool = ToolRegistry.get_tool("text_summarizer")
            prev_results = state.get("result_storage") or []
            source_text = "\n".join([r.get("result", "") for r in prev_results if r.get("result")]) or current_task.description
            if summarizer_tool:
                result_text = invoke_legacy_tool(
                    summarizer_tool,
                    {"text": source_text, "max_bullet_points": 6},
                )
            else:
                result_text = f"Summarized output for: {current_task.description}"

        elif "report" in node or "markdown" in node or "report" in desc or "báo cáo" in desc or "markdown" in desc:
            report_tool = ToolRegistry.get_tool("markdown_report_generator")
            prev_results = state.get("result_storage") or []
            sections = []
            for i, r in enumerate(prev_results, 1):
                desc_title = r.get("description", f"Task {r.get('task_id', i)}")
                short_title = desc_title[:50] + "..." if len(desc_title) > 50 else desc_title
                sections.append({
                    "header": f"Output {r.get('task_id', i)}: {short_title}",
                    "content": r.get("result", "Completed successfully.")
                })
            if not sections:
                sections = [{"header": "Executive Overview", "content": "Task completed successfully with all objectives met."}]

            if report_tool:
                result_text = invoke_legacy_tool(
                    report_tool,
                    {
                        "title": "AgentFlow Comprehensive Intelligence Report",
                        "summary": f"Báo cáo tổng hợp tự động cho quy trình: {current_task.description}",
                        "sections": sections,
                        "filename": "intelligence_report.md",
                    },
                )
            else:
                result_text = "Report generated successfully."
        else:
            result_text = f"Completed task: {current_task.description}"

    except Exception as e:
        status = "failed"
        error_msg = str(e)
        logs.append(f"[WorkerNode] Error executing task: {error_msg}")

    if status == "done" and is_tool_failure(result_text):
        status = "failed"
        normalized_result = parse_tool_result(result_text)
        error_msg = normalized_result.error.message if normalized_result.error else str(result_text)
        logs.append(f"[WorkerNode] Task failed because the tool returned an error: {error_msg}")

    updated_task = current_task.model_copy(update={
        "status": "done" if status == "done" else "failed",
        "error": error_msg
    })

    new_result = {
        "task_id": current_task.id,
        "node": current_task.node,
        "description": current_task.description,
        "result": result_text,
        "status": status,
        "error": error_msg
    }

    updates = {
        "plan": [updated_task],
        "current_task": updated_task,
        "result_storage": [new_result],
        "logs": logs,
    }
    if timing_enabled:
        updates["execution_timings"] = serialize_execution_timings(execution_timings)
    return updates


async def worker_node(
    state: State,
    agent_resolver: AgentResolver | None = None,
    evidence_store: EvidenceStore | None = None,
) -> Dict[str, Any]:
    """Run a worker with a per-task wall-clock limit."""

    current_task = state.get("current_task")
    options = {}
    if agent_resolver is not None:
        options["agent_resolver"] = agent_resolver
    if evidence_store is not None:
        options["evidence_store"] = evidence_store
    worker_execution = _execute_worker_node(state, **options)
    if current_task is None:
        return await worker_execution
    timeout_seconds = current_task.timeout_seconds or settings.MAX_TASK_TIMEOUT_SECONDS
    try:
        return await asyncio.wait_for(
            worker_execution,
            timeout=max(float(timeout_seconds), 0.1),
        )
    except asyncio.TimeoutError:
        failed_task = current_task.model_copy(
            update={
                "status": "failed",
                "error": f"Task exceeded timeout of {timeout_seconds} seconds.",
            }
        )
        return {
            "plan": [failed_task],
            "current_task": failed_task,
            "result_storage": [{
                "task_id": current_task.id,
                "node": current_task.node,
                "description": current_task.description,
                "result": "",
                "status": "failed",
                "error": failed_task.error,
            }],
            "logs": [f"[WorkerNode Error] {failed_task.error}"],
        }


def route_after_supervisor(state: State) -> str:
    """
    Conditional router edge after supervisor:
    - mode='conversation': đi vào 'hitl_gate' (HITL checkpoint node) — graph sẽ PAUSE tại đây.
    - mode='executing': bỏ qua gate, đi thẳng vào 'dispatcher_node'.
    """
    mode = state.get("mode", "conversation")
    if mode == "executing":
        return "dispatcher_node"
    return "hitl_gate"


async def hitl_gate_node(state: State) -> Dict[str, Any]:
    """
    HITL Gate Node — no-op node làm điểm dừng trong conversation mode.

    Sau khi Supervisor đề xuất plan (mode='conversation'), graph route vào node này
    rồi đến END. Checkpointer lưu state tại đây, cho phép resume sau khi user
    approve (bằng cách gửi ainvoke với {"mode": "executing"} và cùng thread_id).

    Node này không thực hiện bất kỳ logic nào — chỉ là checkpoint marker.
    """
    return {"logs": ["[HITLGate] Awaiting user input (approve/reject/chat)."]}  


def route_after_dispatch(state: State) -> str:
    """
    Conditional router edge: decides whether to continue to worker_node or finish graph execution.
    """
    current_task = state.get("current_task")
    if current_task is not None and current_task.status == "running":
        return "worker_node"
    return END


def build_execution_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    agent_resolver: AgentResolver | None = None,
    evidence_store: EvidenceStore | None = None,
) -> "CompiledGraph":
    """
    Constructs and compiles the AgentFlow LangGraph StateGraph.

    Graph được compile với:
    - checkpointer: lưu state tại mỗi bước (mặc định dùng singleton MemorySaver)
    - interrupt_after=["supervisor_node"]: graph tự động PAUSE ngay sau supervisor_node.
      Điều này cho phép user xem xét plan (conversation) hoặc approve trước khi
      dispatcher-worker loop bắt đầu chạy. Sau khi approve, gọi lại ainvoke
      với cùng thread_id để RESUME — dispatcher-worker loop sẽ chạy đến END
      mà không bị interrupt thêm.

    Args:
        checkpointer: Checkpointer tùy chọn (dùng trong tests để inject MemorySaver riêng)
    """
    workflow = StateGraph(State, context_schema=ExecutionContext)
    resolver = agent_resolver or AgentResolver()

    # Add Nodes with the same active catalog resolver used by worker execution.
    async def resolved_supervisor_node(
        state: State,
        runtime: Runtime[ExecutionContext],
    ) -> Dict[str, Any]:
        return await supervisor_node(
            state,
            agent_resolver=resolver,
            on_assistant_token=(runtime.context or {}).get("on_assistant_token"),
        )

    workflow.add_node("supervisor_node", resolved_supervisor_node)
    workflow.add_node("hitl_gate", hitl_gate_node)  # HITL checkpoint: PAUSE khi mode=conversation
    workflow.add_node("dispatcher_node", dispatcher_node)

    async def resolved_worker_node(state: State) -> Dict[str, Any]:
        return await worker_node(state, agent_resolver=resolver, evidence_store=evidence_store)

    workflow.add_node("worker_node", resolved_worker_node)

    # supervisor -> hitl_gate (conversation) | dispatcher (executing)
    workflow.set_entry_point("supervisor_node")
    workflow.add_conditional_edges(
        "supervisor_node",
        route_after_supervisor,
        {
            "hitl_gate": "hitl_gate",
            "dispatcher_node": "dispatcher_node",
        }
    )

    # hitl_gate → END (graph dừng lại, chờ user input tiếp theo qua API)
    workflow.add_edge("hitl_gate", END)

    # dispatcher -> worker | END
    workflow.add_conditional_edges(
        "dispatcher_node",
        route_after_dispatch,
        {
            "worker_node": "worker_node",
            END: END
        }
    )

    workflow.add_edge("worker_node", "dispatcher_node")

    _checkpointer = checkpointer if checkpointer is not None else get_checkpointer()
    return workflow.compile(
        checkpointer=_checkpointer,
        # Không cần interrupt_before/after vì hitl_gate→END đã làm dừng graph ở đúng chỗ.
        # Checkpointer vẫn lưu state sau mỗi node để hỗ trợ resume.
    )


def get_graph_config(run_id: str) -> Dict[str, Any]:
    """
    Tạo LangGraph config dict chuẩn cho một run cụ thể.

    Mỗi run_id ánh xạ đến một thread riêng biệt trong checkpointer,
    đảm bảo state isolation giữa các run song song.

    Args:
        run_id: ID duy nhất của run, dùng làm thread_id

    Returns:
        config dict để truyền vào ainvoke/astream/astream_events
    """
    return {"configurable": {"thread_id": run_id}}
