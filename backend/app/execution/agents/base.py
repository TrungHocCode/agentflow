import re
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.execution.state import State, Task, SupervisorOutput, WorkerOutput


_URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\\\"']+")


def _run_input_context(state: State) -> str:
    """Render user-provided run input as explicit execution context."""

    metadata = state.get("metadata") or {}
    input_data = metadata.get("input_data") or {}
    if not isinstance(input_data, dict):
        input_data = {"value": input_data}

    user_prompt = str(input_data.get("user_prompt") or "").strip()
    raw_urls = input_data.get("urls") or []
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    urls = [
        str(url).strip().rstrip(".,;:!?)]}")
        for url in raw_urls
        if str(url).strip()
    ]
    if user_prompt:
        urls.extend(
            url.rstrip(".,;:!?)]}")
            for url in _URL_PATTERN.findall(user_prompt)
        )
    urls = list(dict.fromkeys(urls))

    if not user_prompt and not urls:
        return "\n--- Run Input ---\nNo explicit user input was provided.\n-----------------\n"

    lines = ["\n--- Run Input (user-provided data) ---"]
    if user_prompt:
        lines.append(f"User request: {user_prompt}")
    if urls:
        lines.append("URLs from user input (use these exact URLs; do not invent placeholders):")
        lines.extend(f"- {url}" for url in urls)
    lines.append("Do not replace a provided URL with example.com or another invented URL.")
    lines.append("---------------------------------------\n")
    return "\n".join(lines)


def _is_tool_error(value: Any) -> bool:
    """Detect normalized tool failures returned as text by current tools."""

    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return lowered.startswith(("error:", "failed:", "http error:"))


class BaseAgent(ABC):
    """
    Base class for all agents in the platform.
    Defines the standard interface for agent execution and state handling.
    """
    def __init__(
        self,
        name: str,
        system_prompt: str,
        llm: BaseChatModel,
        tools: Optional[List[BaseTool]] = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm
        self.tools = tools or []

    def _get_messages(self, state: State) -> List[BaseMessage]:
        """
        Normalize messages from State, converting strings to HumanMessages.
        """
        normalized = []
        for msg in state.get("messages", []):
            if isinstance(msg, str):
                normalized.append(HumanMessage(content=msg))
            elif isinstance(msg, BaseMessage):
                normalized.append(msg)
        return normalized

    @abstractmethod
    async def execute(self, state: State) -> Dict[str, Any]:
        """
        Execute the agent logic based on the current state.
        Should return a dictionary that updates the state.
        """
        pass


class SupervisorAgent(BaseAgent):
    """
    SupervisorAgent is responsible for coordinating conversation, clarifying user intent,
    and formulating/approving execution plans.
    """
    async def execute(self, state: State) -> Dict[str, Any]:
        user_messages = self._get_messages(state)
        last_user_msg = ""
        if user_messages:
            last_user_msg = str(user_messages[-1].content).strip().lower()

        plan = state.get("plan") or []
        current_mode = state.get("mode", "conversation")

        # Approval keyword check: if user approves existing plan, switch mode to executing
        approval_keywords = ["đồng ý", "chạy đi", "ok", "yes", "run", "approve", "bắt đầu", "thực thi"]
        if plan and any(kw in last_user_msg for kw in approval_keywords):
            return {
                "mode": "executing",
                "messages": [AIMessage(content="Đã nhận xác nhận từ bạn! Hệ thống đang chuyển sang chế độ thực thi các task...")],
                "logs": ["[SupervisorAgent] User approved plan. Switching mode to 'executing'."]
            }

        # Formulate current execution plan context
        if plan:
            plan_str = "\n".join([
                f"- Task {t.id}: {t.description} (Node: {t.node}, Status: {t.status})"
                for t in plan
            ])
        else:
            plan_str = "No current plan."

        results = state.get("result_storage") or []
        if results:
            results_str = "\n".join([
                f"- Task {res.get('task_id', '?')} (Node: {res.get('node', '?')}): {res.get('result', '')}"
                for res in results
            ])
        else:
            results_str = "No execution results yet."

        context = (
            f"\n\n--- Current Execution Context ---\n"
            f"[Plan]:\n{plan_str}\n\n"
            f"[Previous Results]:\n{results_str}\n"
            f"---------------------------------"
        )
        system_message = SystemMessage(content=self.system_prompt + context)
        messages = [system_message] + user_messages

        try:
            structured_llm = self.llm.with_structured_output(SupervisorOutput)
            response: SupervisorOutput = await structured_llm.ainvoke(messages)

            updates = {}
            if response.mode:
                updates["mode"] = response.mode
            if response.assistant_message:
                updates["messages"] = [AIMessage(content=response.assistant_message)]
            if response.plan is not None:
                updates["plan"] = response.plan

            # Merge existing metadata (e.g. use_llm, model_name) with LLM metadata
            existing_metadata = state.get("metadata") or {}
            new_metadata = response.metadata or {}
            updates["metadata"] = {**existing_metadata, **new_metadata}

            return updates
        except Exception:
            # Fallback to plain text invocation if structured output is unsupported by local LLM
            response_msg = await self.llm.ainvoke(messages)
            content = response_msg.content if hasattr(response_msg, "content") else str(response_msg)
            return {
                "mode": "conversation",
                "messages": [AIMessage(content=content)],
                "metadata": state.get("metadata") or {},
                "logs": ["[SupervisorAgent] Direct LLM conversational response generated."]
            }



class WorkerAgent(BaseAgent):
    """
    WorkerAgent executes specific tasks.
    It fetches inputs from result_storage and executes tools to complete the task.
    Tool execution outputs are sanitized and wrapped in <tool_output> tags for safety.
    """
    async def execute(self, state: State) -> Dict[str, Any]:
        current_task = state.get("current_task")
        if not current_task:
            raise ValueError(f"Worker '{self.name}' executed but 'current_task' is missing in state.")

        # Build current task context
        task_context = (
            f"\n\n--- Current Task to Execute ---\n"
            f"Task ID: {current_task.id}\n"
            f"Description: {current_task.description}\n"
            f"---------------------------------\n"
        )
        task_context += _run_input_context(state)

        # Build context from previous results
        results = state.get("result_storage") or []
        if results:
            results_str = "\n".join([
                f"- Task {res.get('task_id', '?')} (Node: {res.get('node', '?')}) "
                f"Description: {res.get('description', '?')}\n"
                f"  Result: {res.get('result', '')}"
                for res in results
            ])
        else:
            results_str = "No execution results from other agents yet."

        context_results = (
            f"\n--- Outputs of Previous Tasks (Available Inputs) ---\n"
            f"{results_str}\n"
            f"----------------------------------------------------\n"
        )

        system_message = SystemMessage(content=self.system_prompt + task_context + context_results)
        messages = [system_message] + self._get_messages(state)

        # Execute agent tool loop
        logs = []
        status = "done"
        error_msg = None
        final_result = ""
        last_tool_error: Optional[str] = None

        try:
            if self.tools:
                llm_with_tools = self.llm.bind_tools(self.tools)
            else:
                llm_with_tools = self.llm

            max_iterations = current_task.max_iterations if current_task.max_iterations else 5
            iteration = 0
            tool_map = {tool.name: tool for tool in self.tools}

            while iteration < max_iterations:
                iteration += 1
                logs.append(f"[{self.name}] Iteration {iteration}: Invoking LLM.")
                response = await llm_with_tools.ainvoke(messages)
                messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    logs.append(f"[{self.name}] Tool calls requested: {response.tool_calls}")
                    for tool_call in response.tool_calls:
                        tool_name = tool_call["name"]
                        tool_args = tool_call["args"]
                        tool_id = tool_call["id"]

                        if tool_name in tool_map:
                            tool_obj = tool_map[tool_name]
                            logs.append(f"[{self.name}] Executing tool '{tool_name}' with args {tool_args}")
                            try:
                                # Run tool asynchronously or fallback to sync invoke
                                if hasattr(tool_obj, "_arun") or hasattr(tool_obj, "arun"):
                                    tool_result = await tool_obj.ainvoke(tool_args)
                                else:
                                    tool_result = tool_obj.invoke(tool_args)
                                logs.append(f"[{self.name}] Tool '{tool_name}' result: {tool_result}")
                            except Exception as e:
                                tool_result = f"Error executing tool '{tool_name}': {str(e)}"
                                logs.append(f"[{self.name}] {tool_result}")
                        else:
                            tool_result = f"Tool '{tool_name}' not found in registry."
                            logs.append(f"[{self.name}] {tool_result}")

                        if _is_tool_error(tool_result):
                            last_tool_error = str(tool_result)
                        else:
                            last_tool_error = None

                        # Prompt injection defense: wrap tool output in XML tags
                        wrapped_output = f"<tool_output>\n{str(tool_result)}\n</tool_output>"

                        messages.append(ToolMessage(
                            content=wrapped_output,
                            name=tool_name,
                            tool_call_id=tool_id
                        ))
                else:
                    final_result = response.content
                    break
            else:
                status = "failed"
                error_msg = f"Agent exceeded maximum tool execution iterations ({max_iterations})."
                logs.append(f"[{self.name}] Error: {error_msg}")

            if status == "done" and last_tool_error:
                status = "failed"
                error_msg = last_tool_error
                logs.append(f"[{self.name}] Task failed because the final tool call failed: {error_msg}")

        except Exception as e:
            status = "failed"
            error_msg = str(e)
            logs.append(f"[{self.name}] Exception occurred: {error_msg}")

        # Update state
        new_result = {
            "task_id": current_task.id,
            "node": self.name,
            "description": current_task.description,
            "result": final_result,
            "status": status,
            "error": error_msg
        }

        updated_task = current_task.model_copy(update={
            "status": "done" if status == "done" else "failed",
            "error": error_msg
        })

        return {
            "plan": [updated_task],
            "current_task": updated_task,
            "result_storage": [new_result],
            "logs": logs
        }
