import json
import re
from abc import ABC, abstractmethod
from math import ceil
from typing import Any, Dict, Iterable, List, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.execution.state import State, Task, SupervisorOutput, WorkerOutput
from app.execution.tools.contracts import parse_tool_result


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

        structured_llm = self.llm.with_structured_output(SupervisorOutput)
        response: SupervisorOutput = await structured_llm.ainvoke(messages)

        updates: Dict[str, Any] = {
            "mode": "conversation",
            "messages": [AIMessage(content=response.assistant_message)],
        }
        if response.mode == "executing":
            updates["logs"] = [
                "[SupervisorAgent] Ignored model execution transition; "
                "explicit user approval is required."
            ]
        if response.decision in {"clarify", "propose_plan"}:
            updates["plan"] = response.plan

        # Merge existing metadata (e.g. use_llm and inference purpose) with model metadata.
        existing_metadata = state.get("metadata") or {}
        new_metadata = response.metadata or {}
        merged_metadata = {
            **existing_metadata,
            **new_metadata,
        }
        if "inference_purpose" in existing_metadata:
            merged_metadata["inference_purpose"] = existing_metadata["inference_purpose"]
        merged_metadata["supervisor_decision"] = response.decision
        updates["metadata"] = merged_metadata
        updates["logs"] = updates.get("logs", []) + [
            f"[SupervisorAgent] Produced '{response.decision}' response."
        ]
        return updates



class WorkerAgent(BaseAgent):
    """
    WorkerAgent executes specific tasks.
    It fetches inputs from result_storage and executes tools to complete the task.
    Tool execution outputs are sanitized and wrapped in <tool_output> tags for safety.
    """
    MINIMUM_USABLE_SOURCE_RATIO = 0.5

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
        tool_outcomes: Dict[str, Dict[str, Any]] = {}

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

                        normalized_tool_result = parse_tool_result(
                            tool_result,
                            tool_name=tool_name,
                        )
                        call_key = json.dumps(
                            [tool_name, tool_args],
                            sort_keys=True,
                            ensure_ascii=False,
                            default=str,
                        )
                        previous_outcome = tool_outcomes.get(call_key)
                        outcome = {
                            "tool_name": tool_name,
                            "arguments": tool_args,
                            "result": normalized_tool_result,
                        }
                        # A successful retry for the same arguments recovers an earlier failure;
                        # a later failed retry must not discard a result already obtained.
                        if normalized_tool_result.ok:
                            if not (
                                previous_outcome
                                and previous_outcome["result"].ok
                                and previous_outcome["result"].status == "success"
                            ):
                                tool_outcomes[call_key] = outcome
                        elif not (previous_outcome and previous_outcome["result"].ok):
                            tool_outcomes[call_key] = outcome

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

            if status == "done" and tool_outcomes:
                failed_outcomes = [
                    outcome
                    for outcome in tool_outcomes.values()
                    if not outcome["result"].ok
                ]
                partial_outcomes = [
                    outcome
                    for outcome in tool_outcomes.values()
                    if outcome["result"].ok and outcome["result"].status == "partial"
                ]
                successful_outcomes = [
                    outcome
                    for outcome in tool_outcomes.values()
                    if outcome["result"].ok
                ]

                if failed_outcomes and not successful_outcomes:
                    status = "failed"
                    failures = [
                        outcome["result"].error.message
                        if outcome["result"].error
                        else f"{outcome['tool_name']} returned no usable result."
                        for outcome in failed_outcomes
                    ]
                    error_msg = "; ".join(dict.fromkeys(failures))
                    logs.append(f"[{self.name}] Task failed because all tool calls failed: {error_msg}")
                elif failed_outcomes or partial_outcomes:
                    warnings = self._partial_tool_warnings(failed_outcomes, partial_outcomes)
                    usable_count, source_count = self._tool_outcome_coverage(tool_outcomes.values())
                    minimum_count = max(1, ceil(source_count * self.MINIMUM_USABLE_SOURCE_RATIO))
                    if usable_count < minimum_count:
                        status = "failed"
                        error_msg = (
                            "Insufficient source coverage: "
                            f"{usable_count} of {source_count} sources produced usable evidence; "
                            f"at least {minimum_count} are required. "
                            + "; ".join(warnings)
                        )
                        logs.append(f"[{self.name}] Task failed due to insufficient source coverage.")
                    else:
                        status = "partial"
                        error_msg = (
                            f"Partial source coverage ({usable_count}/{source_count} usable): "
                            + "; ".join(warnings)
                        )
                        logs.append(f"[{self.name}] Task completed with partial tool results: {error_msg}")
                    final_result = f"{str(final_result).rstrip()}\n\n{error_msg}".strip()

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
            "status": status,
            "error": error_msg
        })

        return {
            "plan": [updated_task],
            "current_task": updated_task,
            "result_storage": [new_result],
            "logs": logs
        }

    @classmethod
    def _tool_outcome_coverage(cls, outcomes: Iterable[Dict[str, Any]]) -> tuple[int, int]:
        """Count usable inputs, requiring at least half of a partial batch to proceed."""

        usable_count = 0
        source_count = 0
        for outcome in outcomes:
            result = outcome["result"]
            data = result.data if isinstance(result.data, dict) else {}
            records = data.get("queries")
            record_kind = "queries"
            if not isinstance(records, list):
                records = data.get("sources")
                record_kind = "sources"

            if isinstance(records, list) and records:
                source_count += len(records)
                if record_kind == "queries":
                    usable_count += sum(
                        1
                        for record in records
                        if isinstance(record, dict)
                        and record.get("status") in {"success", "partial"}
                        and int(record.get("result_count") or 0) > 0
                    )
                else:
                    usable_count += sum(
                        1
                        for record in records
                        if isinstance(record, dict) and record.get("ok") is True
                    )
                continue

            if result.status != "partial":
                source_count += 1
                usable_count += int(result.ok)
                continue

            metadata = result.metadata.model_extra or {}
            count_pairs = (
                ("successful_query_count", "query_count"),
                ("successful_source_count", "unique_url_count"),
            )
            counts = next(
                (
                    (metadata.get(success_key), metadata.get(total_key))
                    for success_key, total_key in count_pairs
                    if isinstance(metadata.get(success_key), int)
                    and isinstance(metadata.get(total_key), int)
                ),
                None,
            )
            if counts:
                usable_count += counts[0]
                source_count += counts[1]
            else:
                source_count += 1
                usable_count += int(result.ok)

        return usable_count, source_count

    @staticmethod
    def _partial_tool_warnings(
        failed_outcomes: List[Dict[str, Any]],
        partial_outcomes: List[Dict[str, Any]],
    ) -> List[str]:
        """Describe missing source coverage without dumping large tool payloads."""

        warnings: list[str] = []
        for outcome in failed_outcomes:
            result = outcome["result"]
            arguments = outcome["arguments"]
            subject = arguments.get("query") or arguments.get("url") or outcome["tool_name"]
            message = result.error.message if result.error else "No usable result returned."
            warnings.append(f"{subject}: {message}")

        for outcome in partial_outcomes:
            result = outcome["result"]
            details = []
            data = result.data if isinstance(result.data, dict) else {}
            for collection_name in ("queries", "sources"):
                records = data.get(collection_name)
                if not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    error = record.get("error")
                    if not error and record.get("ok") is not False:
                        continue
                    if isinstance(error, dict):
                        message = error.get("message") or error.get("code") or "failed"
                    else:
                        message = str(error or record.get("status") or "failed")
                    subject = (
                        record.get("query")
                        or record.get("requested_url")
                        or record.get("url")
                        or outcome["tool_name"]
                    )
                    details.append(f"{subject}: {message}")
            if details:
                warnings.extend(details)
            else:
                warnings.append(f"{outcome['tool_name']} returned partial results.")

        return list(dict.fromkeys(warnings))
