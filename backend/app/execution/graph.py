from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END

from app.execution.state import State, Task
from app.execution.nodes.dispatcher import TaskDispatcher
from app.execution.agents.base import SupervisorAgent, WorkerAgent
from app.execution.tools.base import ToolRegistry
from app.execution.tools.registry import autodiscover_tools

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
        from app.execution.llm import get_llm
        model_name = metadata.get("model_name", "qwen3:8b")
        llm = get_llm(model_name=model_name, temperature=0.2)
        supervisor = SupervisorAgent(
            name="supervisor",
            system_prompt=SUPERVISOR_SYSTEM_PROMPT,
            llm=llm
        )
        return await supervisor.execute(state)

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


async def worker_node(state: State) -> Dict[str, Any]:
    """
    Worker Node executes the current dispatched task using configured tools.
    """
    current_task = state.get("current_task")
    if not current_task:
        return {"logs": ["[WorkerNode] No current_task found in state to execute."]}

    node_name = current_task.node.lower()
    tool_instances = []

    all_registered_tools = ToolRegistry.get_all_tools()

    if "crawler" in node_name or "news" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("news_crawler", "web_search", "http_request")]
    elif "summarizer" in node_name or "summary" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("text_summarizer", "file_reader")]
    elif "report" in node_name or "writer" in node_name or "coder" in node_name:
        tool_instances = [t for t in all_registered_tools if t.name in ("markdown_report_generator", "python_executor", "file_writer")]
    else:
        tool_instances = all_registered_tools

    logs = [f"[WorkerNode] Executing Task {current_task.id} ('{current_task.description}') on node '{current_task.node}' with {len(tool_instances)} tools."]

    metadata = state.get("metadata") or {}
    use_llm = metadata.get("use_llm", False)

    if use_llm:
        from app.execution.llm import get_llm
        model_name = metadata.get("model_name", "qwen3:8b")
        logs.append(f"[WorkerNode] Initializing live Ollama LLM ({model_name}) for ReAct loop.")
        llm = get_llm(model_name=model_name)
        worker_agent = WorkerAgent(
            name=current_task.node,
            system_prompt=f"You are a specialized Worker Agent executing node '{current_task.node}'. Complete the assigned task using your tools.",
            llm=llm,
            tools=tool_instances
        )
        return await worker_agent.execute(state)

    result_text = ""
    status = "done"
    error_msg = None

    try:
        desc = current_task.description.lower()
        if "crawl" in desc or "news" in desc or "http" in desc:
            crawler_tool = ToolRegistry.get_tool("news_crawler")
            if crawler_tool:
                url_match = [w for w in current_task.description.split() if w.startswith("http")]
                target_url = url_match[0] if url_match else "https://news.ycombinator.com"
                result_text = crawler_tool.invoke({"url": target_url})
            else:
                result_text = f"Executed task '{current_task.description}' successfully."

        elif "summariz" in desc or "summary" in desc:
            summarizer_tool = ToolRegistry.get_tool("text_summarizer")
            prev_results = state.get("result_storage") or []
            source_text = "\n".join([r.get("result", "") for r in prev_results]) or current_task.description
            if summarizer_tool:
                result_text = summarizer_tool.invoke({"text": source_text, "max_bullet_points": 5})
            else:
                result_text = f"Summarized output for: {current_task.description}"

        elif "report" in desc or "markdown" in desc:
            report_tool = ToolRegistry.get_tool("markdown_report_generator")
            prev_results = state.get("result_storage") or []
            sections = [
                {"header": f"Task Output {r.get('task_id', i)}", "content": r.get("result", "")}
                for i, r in enumerate(prev_results, 1)
            ]
            if not sections:
                sections = [{"header": "Overview", "content": "Completed task analysis report."}]

            if report_tool:
                result_text = report_tool.invoke({
                    "title": "AgentFlow Intelligence Report",
                    "summary": "Summary report generated automatically by WorkerAgent.",
                    "sections": sections,
                    "filename": "intelligence_report.md"
                })
            else:
                result_text = "Report generated successfully."
        else:
            result_text = f"Worker completed task: {current_task.description}"

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


def route_after_supervisor(state: State) -> str:
    """
    Conditional router edge after supervisor:
    If mode is 'executing', proceed to dispatcher_node.
    If mode is 'conversation', pause execution and return to user (END).
    """
    mode = state.get("mode", "conversation")
    if mode == "executing":
        return "dispatcher_node"
    return END


def route_after_dispatch(state: State) -> str:
    """
    Conditional router edge: decides whether to continue to worker_node or finish graph execution.
    """
    current_task = state.get("current_task")
    if current_task is not None and current_task.status == "running":
        return "worker_node"
    return END


def build_execution_graph():
    """
    Constructs and compiles the AgentFlow LangGraph StateGraph.
    """
    workflow = StateGraph(State)

    # Add Nodes
    workflow.add_node("supervisor_node", supervisor_node)
    workflow.add_node("dispatcher_node", dispatcher_node)
    workflow.add_node("worker_node", worker_node)

    # Add Edges
    workflow.set_entry_point("supervisor_node")
    workflow.add_conditional_edges(
        "supervisor_node",
        route_after_supervisor,
        {
            "dispatcher_node": "dispatcher_node",
            END: END
        }
    )

    workflow.add_conditional_edges(
        "dispatcher_node",
        route_after_dispatch,
        {
            "worker_node": "worker_node",
            END: END
        }
    )

    workflow.add_edge("worker_node", "dispatcher_node")

    return workflow.compile()

