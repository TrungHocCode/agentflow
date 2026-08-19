from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END

from app.execution.state import State, Task
from app.execution.nodes.dispatcher import TaskDispatcher
from app.execution.agents.base import SupervisorAgent, WorkerAgent
from app.execution.tools.base import ToolRegistry
from app.execution.tools.registry import autodiscover_tools

# Ensure all tools are registered
autodiscover_tools()


async def supervisor_node(state: State) -> Dict[str, Any]:
    """
    Supervisor Node handles intent analysis and plan formulation during Build Phase.
    If state already has a plan, it preserves the existing plan.
    """
    plan = state.get("plan") or []
    logs = [f"[SupervisorNode] Processing state. Current plan tasks count: {len(plan)}."]
    return {
        "mode": "executing",
        "logs": logs
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

    # Resolve tool instances from registry
    # Map common node types to default tool sets if unspecified
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
        # Run specific tool logic based on task description keywords
        desc = current_task.description.lower()
        if "crawl" in desc or "news" in desc or "http" in desc:
            crawler_tool = ToolRegistry.get_tool("news_crawler")
            if crawler_tool:
                # Extract URL if present in description or fallback to test URL
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
    workflow.add_edge("supervisor_node", "dispatcher_node")

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
