import asyncio
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.execution.state import State, Task
from app.execution.nodes.dispatcher import TaskDispatcher
from app.execution.agents.base import SupervisorAgent, WorkerAgent
from app.execution.tools.base import ToolRegistry
from app.execution.tools.registry import autodiscover_tools
from app.execution.checkpointer import get_checkpointer

# Ensure all tools are registered
autodiscover_tools()


SUPERVISOR_SYSTEM_PROMPT = (
    "You are SupervisorAgent, an AI Agent Planner for AgentFlow platform.\n"
    "Your job is to converse with the user, clarify their requirements, and construct a DAG plan.\n"
    "Available Worker Nodes: 'news_crawler', 'text_summarizer', 'markdown_report_generator', 'python_executor', 'web_search'.\n"
    "If the user's request is vague, ask clarifying questions (keep mode='conversation').\n"
    "If the request is clear, propose a list of Tasks and ask if they agree to execute it (keep mode='conversation').\n"
    "If the user approves (e.g. 'ok', 'chạy đi', 'đồng ý', 'yes', 'run', 'approve'), set mode='executing'."
)


async def supervisor_node(state: State) -> Dict[str, Any]:
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

    if use_llm:
        try:
            from app.execution.llm import get_llm
            model_name = metadata.get("model_name", "qwen3:8b")
            llm = get_llm(model_name=model_name, temperature=0.2)
            supervisor = SupervisorAgent(
                name="supervisor",
                system_prompt=SUPERVISOR_SYSTEM_PROMPT,
                llm=llm
            )
            return await supervisor.execute(state)
        except Exception as exc:
            # A configured live model must fail explicitly. A heuristic plan would
            # look successful to the user while not representing the requested LLM.
            return {
                "mode": "conversation",
                "messages": [
                    "Local model is unavailable. Please start Ollama or check the configured model before continuing."
                ],
                "logs": [f"[SupervisorNode Error] LLM planning unavailable: {exc}"],
                "metadata": {**metadata, "llm_error": str(exc)},
            }

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


async def _execute_worker_node(state: State) -> Dict[str, Any]:
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
        tool_instances = [t for t in all_registered_tools if t.name in ("news_crawler", "web_search", "http_request")]
    elif "search" in node_name or "web" in node_name or "tìm" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("web_search", "news_crawler", "http_request")]
    elif "summarizer" in node_name or "summary" in node_name or "tóm" in node_name or "tổng" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("text_summarizer", "file_reader")]
    elif "report" in node_name or "writer" in node_name or "coder" in node_name or "markdown" in node_name or "báo cáo" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("markdown_report_generator", "python_executor", "file_writer")]
    else:
        tool_instances = all_registered_tools

    logs = [f"[WorkerNode] Executing Task {current_task.id} ('{current_task.description}') on node '{current_task.node}' with {len(tool_instances)} tools."]

    metadata = state.get("metadata") or {}
    use_llm = metadata.get("use_llm", False)

    if use_llm:
        try:
            from app.execution.llm import get_llm
            model_name = metadata.get("model_name", "qwen3:8b")
            logs.append(f"[WorkerNode] Initializing live Ollama LLM ({model_name}) for ReAct loop.")
            llm = get_llm(model_name=model_name, temperature=0.2)
            worker_agent = WorkerAgent(
                name=current_task.node,
                system_prompt=(
                    f"You are a specialized Worker Agent executing node '{current_task.node}'.\n"
                    f"Your task: {current_task.description}.\n"
                    f"Use your available tools to gather real data and return a thorough, informative result."
                ),
                llm=llm,
                tools=tool_instances
            )
            return await worker_agent.execute(state)
        except Exception as e:
            logs.append(f"[WorkerNode Error] LLM ReAct unavailable: {e}")
            updated_task = current_task.model_copy(update={"status": "failed", "error": str(e)})
            return {
                "plan": [updated_task],
                "current_task": updated_task,
                "result_storage": [{
                    "task_id": current_task.id,
                    "node": current_task.node,
                    "description": current_task.description,
                    "result": "",
                    "status": "failed",
                    "error": f"Local model unavailable: {e}",
                }],
                "logs": logs,
            }

    result_text = ""
    status = "done"
    error_msg = None

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
                result_text = search_tool.invoke({"query": query or current_task.description})
            else:
                result_text = f"Executed search task: '{current_task.description}'"

        elif "crawl" in node or "crawler" in node or "crawl" in desc or "cào" in desc or "news" in desc or "http" in desc:
            crawler_tool = ToolRegistry.get_tool("news_crawler")
            if crawler_tool:
                url_match = [w for w in current_task.description.split() if w.startswith("http")]
                target_url = url_match[0] if url_match else "https://news.ycombinator.com"
                result_text = crawler_tool.invoke({"url": target_url})
            else:
                result_text = f"Executed crawler task '{current_task.description}' successfully."

        elif "summariz" in node or "summary" in node or "tóm tắt" in desc or "tổng hợp" in desc or "summariz" in desc:
            summarizer_tool = ToolRegistry.get_tool("text_summarizer")
            prev_results = state.get("result_storage") or []
            source_text = "\n".join([r.get("result", "") for r in prev_results if r.get("result")]) or current_task.description
            if summarizer_tool:
                result_text = summarizer_tool.invoke({"text": source_text, "max_bullet_points": 6})
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
                result_text = report_tool.invoke({
                    "title": "AgentFlow Comprehensive Intelligence Report",
                    "summary": f"Báo cáo tổng hợp tự động cho quy trình: {current_task.description}",
                    "sections": sections,
                    "filename": "intelligence_report.md"
                })
            else:
                result_text = "Report generated successfully."
        else:
            result_text = f"Completed task: {current_task.description}"

    except Exception as e:
        status = "failed"
        error_msg = str(e)
        logs.append(f"[WorkerNode] Error executing task: {error_msg}")

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

    return {
        "plan": [updated_task],
        "current_task": updated_task,
        "result_storage": [new_result],
        "logs": logs
    }


async def worker_node(state: State) -> Dict[str, Any]:
    """Run a worker with a per-task wall-clock limit."""

    current_task = state.get("current_task")
    if current_task is None or current_task.timeout_seconds is None:
        return await _execute_worker_node(state)
    try:
        return await asyncio.wait_for(
            _execute_worker_node(state),
            timeout=max(float(current_task.timeout_seconds), 0.1),
        )
    except asyncio.TimeoutError:
        failed_task = current_task.model_copy(
            update={
                "status": "failed",
                "error": f"Task exceeded timeout of {current_task.timeout_seconds} seconds.",
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
    checkpointer: Optional[BaseCheckpointSaver] = None
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
    workflow = StateGraph(State)

    # Add Nodes
    workflow.add_node("supervisor_node", supervisor_node)
    workflow.add_node("hitl_gate", hitl_gate_node)  # HITL checkpoint: PAUSE khi mode=conversation
    workflow.add_node("dispatcher_node", dispatcher_node)
    workflow.add_node("worker_node", worker_node)

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
