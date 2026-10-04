import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.core.config import settings
from app.execution.model_router import model_name_for
from app.execution.state import State, Task, SupervisorOutput, WorkerOutput
from app.execution.tools.contracts import parse_tool_result
from app.execution.context_budget import ContextBudgetExceeded, guard_context, project_tool_result
from app.execution.research_evidence import EvidenceProcessor, EvidenceStore
from app.execution.research_contracts import ResearchResult
from app.execution.research_reduction import EvidenceReducer
from app.shared.execution_metrics import ExecutionTiming, serialize_execution_timings
from app.shared.llm_call_metrics import LLMCallObserver
from app.shared.observability import bind_context
from app.shared.task_metrics import TaskExecutionMetric


_URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\\\"']+")
logger = logging.getLogger(__name__)


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


def _research_question(state: State, task: Task) -> str:
    """Pass the user's research request, not task-routing instructions or the run-input wrapper."""
    input_data = (state.get("metadata") or {}).get("input_data") or {}
    prompt = input_data.get("user_prompt") if isinstance(input_data, dict) else None
    return str(prompt).strip() if prompt and str(prompt).strip() else task.description


def _state_model_name(state: State, llm: BaseChatModel | None = None) -> str | None:
    actual_model = getattr(llm, "model", None)
    if isinstance(actual_model, str) and actual_model:
        return actual_model

    metadata = state.get("metadata") or {}
    resolved_config = metadata.get("resolved_model_config") or {}
    model_name = (
        metadata.get("model_name")
        or resolved_config.get("model_name")
        or resolved_config.get("model")
    )
    if model_name:
        return str(model_name)

    purpose = metadata.get("inference_purpose")
    return model_name_for(purpose) if purpose else None


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
    async def execute(
        self,
        state: State,
        on_assistant_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> Dict[str, Any]:
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

        structured_llm = self.llm.with_structured_output(
            SupervisorOutput.model_json_schema() if on_assistant_token else SupervisorOutput,
            **({"method": "json_schema"} if on_assistant_token else {}),
        )
        timing_enabled = settings.ENABLE_EXECUTION_BENCHMARK_METRICS
        llm_started_at = datetime.now(timezone.utc) if timing_enabled else None
        llm_started = perf_counter() if timing_enabled else None
        llm_call_id = str(uuid.uuid4())
        llm_model = _state_model_name(state, self.llm)
        llm_observer = (
            LLMCallObserver(
                call_id=llm_call_id,
                component=self.name,
                purpose="planner",
                model=llm_model,
            )
            if timing_enabled
            else None
        )
        llm_call_metrics: list[dict[str, Any]] = []
        llm_config = {"callbacks": [llm_observer]} if llm_observer else None
        llm_call_started = llm_started if llm_started is not None else perf_counter()
        with bind_context(llm_call_id=llm_call_id):
            logger.info(
                "Supervisor LLM call started",
                extra={"model_name": llm_model, "purpose": "planner"},
            )
            try:
                if on_assistant_token:
                    streamed_output: Dict[str, Any] | None = None
                    streamed_assistant_message = ""
                    output_stream = (
                        structured_llm.astream(messages, config=llm_config)
                        if llm_config
                        else structured_llm.astream(messages)
                    )
                    async for partial_output in output_stream:
                        if not isinstance(partial_output, dict):
                            continue
                        streamed_output = partial_output
                        assistant_message = partial_output.get("assistant_message")
                        if not isinstance(assistant_message, str):
                            continue
                        if assistant_message.startswith(streamed_assistant_message):
                            delta = assistant_message[len(streamed_assistant_message):]
                            if delta:
                                await on_assistant_token(delta)
                                streamed_assistant_message = assistant_message
                    if streamed_output is None:
                        raise ValueError("Supervisor streaming returned no structured output.")
                    response: SupervisorOutput = SupervisorOutput.model_validate(streamed_output)
                else:
                    response = (
                        await structured_llm.ainvoke(messages, config=llm_config)
                        if llm_config
                        else await structured_llm.ainvoke(messages)
                    )
            except Exception as exc:
                llm_duration_ms = round((perf_counter() - llm_call_started) * 1000, 3)
                if llm_observer:
                    failed_metric = llm_observer.to_metric(
                        status="failed",
                        error_type=type(exc).__name__,
                    )
                    llm_call_metrics.append(failed_metric.model_dump(mode="json"))
                    setattr(exc, "llm_call_metrics", llm_call_metrics)
                logger.error(
                    "Supervisor LLM call failed",
                    extra={
                        "model_name": llm_model,
                        "purpose": "planner",
                        "duration_ms": llm_duration_ms,
                        "error_type": type(exc).__name__,
                        "error_code": "supervisor_llm_call_failed",
                    },
                )
                raise
            llm_duration_ms = round((perf_counter() - llm_call_started) * 1000, 3)
            if llm_observer:
                llm_call_metrics.append(
                    llm_observer.to_metric(status="success").model_dump(mode="json")
                )
            logger.info(
                "Supervisor LLM call completed",
                extra={
                    "model_name": llm_model,
                    "purpose": "planner",
                    "duration_ms": llm_duration_ms,
                },
            )

        updates: Dict[str, Any] = {
            "mode": "conversation",
            "messages": [AIMessage(content=response.assistant_message)],
        }
        if timing_enabled and llm_started_at is not None and llm_started is not None:
            timing = ExecutionTiming(
                operation="llm",
                phase="plan",
                name=self.name,
                agent_name=self.name,
                iteration=1,
                model=_state_model_name(state, self.llm),
                duration_ms=llm_duration_ms,
                status="success",
                started_at=llm_started_at,
                completed_at=datetime.now(timezone.utc),
            )
            updates["execution_timings"] = serialize_execution_timings([timing])
        if llm_call_metrics:
            updates["llm_call_metrics"] = llm_call_metrics
        if response.mode == "executing":
            updates["logs"] = [
                "[SupervisorAgent] Ignored model execution transition; "
                "explicit user approval is required."
            ]
        if response.decision in {"clarify", "propose_plan"}:
            updates["plan"] = [task.to_runtime_task() for task in response.plan]

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

    evidence_store: EvidenceStore | None = None

    async def execute(self, state: State) -> Dict[str, Any]:
        current_task = state.get("current_task")
        if not current_task:
            raise ValueError(f"Worker '{self.name}' executed but 'current_task' is missing in state.")
        if self.name == "synthesis_agent" and self.evidence_store is not None:
            return await self._synthesize(state, current_task)
        timing_enabled = settings.ENABLE_EXECUTION_BENCHMARK_METRICS
        task_started_at = datetime.now(timezone.utc) if timing_enabled else None
        task_started = perf_counter() if timing_enabled else None

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
        results = [result for result in results if str(result.get("task_id")) in
                   {str(task_id) for task_id in current_task.dependencies}]
        if results:
            results_str = "\n".join([
                f"- Task {res.get('task_id', '?')} (Node: {res.get('node', '?')}) "
                f"Description: {res.get('description', '?')}\n"
                f"  Result: {res.get('result', '')}"
                for res in results
            ])
        else:
            results_str = "No execution results from other agents yet."

        citation_urls: set[str] = set()
        for result in results:
            content = result.get("result")
            if isinstance(content, dict):
                citation_urls.update(value for value in (content.get("sources") or {}).values()
                                     if isinstance(value, str))
                citation_urls.update(claim["source_url"] for claim in content.get("claims", [])
                                     if isinstance(claim, dict) and isinstance(claim.get("source_url"), str))

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
        tool_artifact_paths: set[str] = set()
        worker_error_metadata: Dict[str, Any] | None = None
        worker_error_id: str | None = None
        execution_timings: List[ExecutionTiming] = []
        llm_call_metrics: list[dict[str, Any]] = []
        metadata = state.get("metadata") or {}
        processor = (
            EvidenceProcessor(self.llm, self.evidence_store, metadata["run_id"], str(current_task.id))
            if self.evidence_store is not None and self.name == "source_researcher" and metadata.get("run_id")
            else None
        )

        try:
            if self.tools:
                llm_with_tools = self.llm.bind_tools(self.tools)
            else:
                llm_with_tools = self.llm

            max_iterations = current_task.max_iterations or settings.MAX_TASK_ITERATIONS
            iteration = 0
            tool_map = {tool.name: tool for tool in self.tools}

            while iteration < max_iterations:
                iteration += 1
                if self.evidence_store is not None and metadata.get("run_id"):
                    await self.evidence_store.check_active(metadata["run_id"])
                estimated_input = guard_context(messages, self.tools)
                logger.info("Worker context budget checked", extra={
                    "estimated_input_tokens": estimated_input,
                    "token_counting_method": "utf8/2_heuristic",
                    "context_tokens": settings.LLM_CONTEXT_TOKENS,
                    "output_reservation": settings.LLM_OUTPUT_TOKENS,
                })
                logs.append(f"[{self.name}] Iteration {iteration}: Invoking LLM.")
                llm_started_at = datetime.now(timezone.utc) if timing_enabled else None
                llm_started = perf_counter() if timing_enabled else None
                llm_call_id = str(uuid.uuid4())
                llm_model = _state_model_name(state, self.llm)
                llm_observer = (
                    LLMCallObserver(
                        call_id=llm_call_id,
                        component=self.name,
                        purpose="worker",
                        model=llm_model,
                        task_id=current_task.id,
                        iteration=iteration,
                    )
                    if timing_enabled
                    else None
                )
                llm_config = {"callbacks": [llm_observer]} if llm_observer else None
                llm_call_started = llm_started if llm_started is not None else perf_counter()
                try:
                    with bind_context(
                        task_execution_id=str(current_task.id),
                        llm_call_id=llm_call_id,
                    ):
                        logger.info(
                            "Worker LLM call started",
                            extra={
                                "model_name": llm_model,
                                "purpose": "worker",
                                "iteration": iteration,
                            },
                        )
                        response = (
                            await llm_with_tools.ainvoke(messages, config=llm_config)
                            if llm_config
                            else await llm_with_tools.ainvoke(messages)
                        )
                        llm_duration_ms = round(
                            (perf_counter() - llm_call_started) * 1000,
                            3,
                        )
                        logger.info(
                            "Worker LLM call completed",
                            extra={
                                "model_name": llm_model,
                                "purpose": "worker",
                                "iteration": iteration,
                                "duration_ms": llm_duration_ms,
                            },
                        )
                except Exception as exc:
                    llm_duration_ms = round((perf_counter() - llm_call_started) * 1000, 3)
                    if llm_observer:
                        llm_call_metrics.append(
                            llm_observer.to_metric(
                                status="failed",
                                error_type=type(exc).__name__,
                            ).model_dump(mode="json")
                        )
                    worker_error_id = str(uuid.uuid4())
                    with bind_context(
                        task_execution_id=str(current_task.id),
                        llm_call_id=llm_call_id,
                    ):
                        logger.error(
                            "Worker LLM call failed",
                            extra={
                                "model_name": llm_model,
                                "purpose": "worker",
                                "iteration": iteration,
                                "duration_ms": llm_duration_ms,
                                "error_type": type(exc).__name__,
                                "error_id": worker_error_id,
                                "error_code": "worker_llm_call_failed",
                            },
                        )
                    if timing_enabled and llm_started_at is not None and llm_started is not None:
                        execution_timings.append(
                            ExecutionTiming(
                                operation="llm",
                                phase="execute",
                                name=self.name,
                                agent_name=self.name,
                                task_id=current_task.id,
                                iteration=iteration,
                                model=_state_model_name(state, self.llm),
                                duration_ms=llm_duration_ms,
                                status="failed",
                                error_type=type(exc).__name__,
                                started_at=llm_started_at,
                                completed_at=datetime.now(timezone.utc),
                            )
                        )
                    raise
                if llm_observer:
                    llm_call_metrics.append(
                        llm_observer.to_metric(status="success").model_dump(mode="json")
                    )
                if timing_enabled and llm_started_at is not None and llm_started is not None:
                    execution_timings.append(
                        ExecutionTiming(
                            operation="llm",
                            phase="execute",
                            name=self.name,
                            agent_name=self.name,
                            task_id=current_task.id,
                            iteration=iteration,
                            model=_state_model_name(state, self.llm),
                            duration_ms=llm_duration_ms,
                            status="success",
                            started_at=llm_started_at,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    logs.append(f"[{self.name}] Tool calls requested: {len(response.tool_calls)}.")
                    for tool_call in response.tool_calls:
                        tool_name = tool_call["name"]
                        tool_args = tool_call["args"]
                        tool_id = tool_call["id"]
                        tool_wall_started = perf_counter()

                        if tool_name in tool_map:
                            if self.name == "report_agent" and self.evidence_store is not None:
                                supplied_urls = {url.rstrip(".,;:!?)]}") for url in _URL_PATTERN.findall(
                                    json.dumps(tool_args, ensure_ascii=False))}
                                if not citation_urls or not supplied_urls.issubset(citation_urls):
                                    raise ValueError("Report citations must refer to collected source evidence.")
                            tool_obj = tool_map[tool_name]
                            logs.append(f"[{self.name}] Executing tool '{tool_name}'.")
                            tool_started_at = datetime.now(timezone.utc) if timing_enabled else None
                            tool_started = tool_wall_started if timing_enabled else None
                            tool_error_type = None
                            try:
                                with bind_context(
                                    task_execution_id=str(current_task.id),
                                    tool_call_id=tool_id,
                                ):
                                    logger.info(
                                        "Worker tool invocation started",
                                        extra={"tool_name": tool_name, "purpose": "worker"},
                                    )
                                    if hasattr(tool_obj, "_arun") or hasattr(tool_obj, "arun"):
                                        tool_result = await tool_obj.ainvoke(tool_args)
                                    else:
                                        tool_result = tool_obj.invoke(tool_args)
                            except Exception as exc:
                                tool_error_type = type(exc).__name__
                                tool_result = f"Error: tool execution failed ({tool_error_type})."
                                logs.append(
                                    f"[{self.name}] Tool '{tool_name}' failed ({tool_error_type})."
                                )
                                logger.error(
                                    "Worker tool invocation failed",
                                    exc_info=(type(exc), exc, exc.__traceback__),
                                    extra={
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_id,
                                        "task_execution_id": str(current_task.id),
                                    },
                                )
                        else:
                            tool_started_at = datetime.now(timezone.utc) if timing_enabled else None
                            tool_started = perf_counter() if timing_enabled else None
                            tool_error_type = "ToolNotFound"
                            tool_result = "Error: requested tool is unavailable."
                            logs.append(f"[{self.name}] Requested tool is unavailable.")
                            logger.warning(
                                "Worker requested a tool that is not authorized",
                                extra={
                                    "tool_name": tool_name,
                                    "tool_call_id": tool_id,
                                    "task_execution_id": str(current_task.id),
                                    "error_code": "tool_unavailable",
                                },
                            )

                        normalized_tool_result = parse_tool_result(tool_result, tool_name=tool_name)
                        tool_duration_ms = round((perf_counter() - tool_wall_started) * 1000, 3)
                        with bind_context(
                            task_execution_id=str(current_task.id),
                            tool_call_id=tool_id,
                        ):
                            logger.info(
                                "Worker tool invocation completed",
                                extra={
                                    "tool_name": tool_name,
                                    "purpose": "worker",
                                    "tool_status": normalized_tool_result.status,
                                    "duration_ms": tool_duration_ms,
                                },
                            )
                        logs.append(
                            f"[{self.name}] Tool '{tool_name}' finished with "
                            f"status '{normalized_tool_result.status}'."
                        )
                        tool_data = normalized_tool_result.data
                        if isinstance(tool_data, dict):
                            for path_key in ("file_path", "svg_path", "spec_path"):
                                path_value = tool_data.get(path_key)
                                if isinstance(path_value, str) and path_value.strip():
                                    tool_artifact_paths.add(path_value.strip())
                        artifact_text = (
                            tool_data.get("text", "")
                            if isinstance(tool_data, dict)
                            else str(tool_data or "")
                        )
                        for artifact_match in re.findall(
                            r"(?:File Path|Chart Spec Path):\s*([^,\n]+)",
                            artifact_text,
                        ):
                            tool_artifact_paths.add(artifact_match.strip().strip("`\"'"))
                        if timing_enabled and tool_started_at is not None and tool_started is not None:
                            execution_timings.append(
                                ExecutionTiming(
                                    operation="tool",
                                    phase="execute",
                                    name=tool_name,
                                    agent_name=self.name,
                                    task_id=current_task.id,
                                    iteration=iteration,
                                    call_id=tool_id,
                                    duration_ms=tool_duration_ms,
                                    status="failed" if tool_error_type else "success",
                                    result_status=(
                                        "success"
                                        if normalized_tool_result.status == "success"
                                        else "partial"
                                        if normalized_tool_result.status == "partial"
                                        else "failed"
                                    ),
                                    error_type=tool_error_type,
                                    started_at=tool_started_at,
                                    completed_at=datetime.now(timezone.utc),
                                )
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
                        observation = normalized_tool_result
                        if processor is not None and tool_name in {"web_search", "web_search_batch"}:
                            discovery_id = await self.evidence_store.save_discovery(
                                metadata["run_id"], str(current_task.id), normalized_tool_result)
                            observation = normalized_tool_result.model_copy(update={"metadata":
                                normalized_tool_result.metadata.model_copy(update={"discovery_id": discovery_id})})
                        if processor is not None and tool_name in {"news_crawler", "news_crawler_batch"}:
                            observation = await processor.process(
                                normalized_tool_result, _research_question(state, current_task))
                        elif processor is not None and tool_name == "http_request":
                            observation = await processor.process_http(
                                normalized_tool_result, _research_question(state, current_task),
                                method=str(tool_args.get("method") or "GET"))
                        wrapped_output = f"<tool_output>\n{project_tool_result(observation)}\n</tool_output>"

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
                    error_msg = f"All {len(failed_outcomes)} tool calls failed."
                    logs.append(f"[{self.name}] Task failed because all tool calls failed.")
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
            context_error = isinstance(e, ContextBudgetExceeded) or "exceed_context_size_error" in str(e)
            error_code = "context_budget_exceeded" if context_error else "worker_execution_failed"
            error_msg = (
                "Research input exceeded the model context budget. No oversized request was retried."
                if context_error else f"Worker execution failed ({type(e).__name__})."
            )
            error_id = worker_error_id or str(uuid.uuid4())
            logs.append(f"[{self.name}] Execution failed ({type(e).__name__}).")
            logger.error(
                "Worker agent execution failed",
                exc_info=(type(e), e, e.__traceback__),
                extra={
                    "task_execution_id": str(current_task.id),
                    "error_id": error_id,
                    "error_code": error_code,
                },
            )
            worker_error_metadata = {
                **(state.get("metadata") or {}),
                "last_error": {
                    "error_id": error_id,
                    "code": error_code,
                    "category": "execution",
                    "retryable": not context_error,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            }

        if processor is not None:
            llm_call_metrics.extend(processor.metrics)
            for metric in processor.metrics:
                execution_timings.append(ExecutionTiming(operation="llm", phase="execute",
                    name="evidence_mapper", agent_name=self.name, task_id=current_task.id,
                    call_id=metric["call_id"], duration_ms=metric["request_latency_ms"], status=metric["status"],
                    model=metric.get("model"), started_at=metric["started_at"], completed_at=metric["completed_at"]))
            final_result = processor.bundle.model_dump(mode="json")
            if status != "failed":
                if not processor.bundle.claims:
                    status = "failed"
                    error_msg = "No validated source-backed evidence was collected; search results alone are insufficient."
                elif processor.bundle.status != "complete":
                    status = "partial"
                    error_msg = "Evidence extraction is partial; inspect chunk coverage and warnings."

        # Update state
        new_result = {
            "task_id": current_task.id,
            "node": self.name,
            "description": current_task.description,
            "result": final_result,
            "status": status,
            "error": error_msg,
            "artifact_paths": sorted(tool_artifact_paths),
        }

        updated_task = current_task.model_copy(update={
            "status": status,
            "error": error_msg
        })

        updates = {
            "plan": [updated_task],
            "current_task": updated_task,
            "result_storage": [new_result],
            "logs": logs,
        }
        if worker_error_metadata is not None:
            updates["metadata"] = worker_error_metadata
        if timing_enabled and task_started_at is not None and task_started is not None:
            updates["execution_timings"] = serialize_execution_timings(execution_timings)
            updates["llm_call_metrics"] = llm_call_metrics
            task_completed_at = datetime.now(timezone.utc)
            updates["task_execution_metrics"] = [
                TaskExecutionMetric(
                    task_id=current_task.id,
                    node=self.name,
                    status=status,
                    duration_ms=round((perf_counter() - task_started) * 1000, 3),
                    started_at=task_started_at,
                    completed_at=task_completed_at,
                ).model_dump(mode="json")
            ]
        return updates

    async def _synthesize(self, state: State, task: Task) -> Dict[str, Any]:
        """Synthesis is reconciliation over typed evidence, not another generic summarizer loop."""
        started = perf_counter()
        started_at = datetime.now(timezone.utc)
        metadata = state.get("metadata") or {}
        reducer = EvidenceReducer(self.llm, self.evidence_store, metadata.get("run_id", ""), task.id)
        error = None
        status = "done"
        result: dict[str, Any] = {}
        try:
            inputs = [item for item in state.get("result_storage", [])
                      if str(item.get("task_id")) in {str(identity) for identity in task.dependencies}]
            bundles = [ResearchResult.model_validate(item["result"]) for item in inputs]
            claims = {claim.evidence_id: claim for bundle in bundles for claim in bundle.claims}
            reduced = await reducer.reduce(list(claims.values()), task.description)
            used_ids = {identity for finding in reduced.findings for identity in finding.evidence_ids}
            result = {**reduced.model_dump(), "sources": {
                identity: claims[identity].source_url for identity in sorted(used_ids)},
                "input_claim_count": len(claims),
                "limitations": list(dict.fromkeys(reduced.limitations + [warning for bundle in bundles
                    for warning in bundle.warnings] + [f"Missing field: {field}" for bundle in bundles
                    for field in bundle.missing_fields])),
            }
            if any(bundle.status != "complete" for bundle in bundles):
                status = "partial"
            result["status"] = status
        except Exception as exc:
            status = "failed"
            error = "Could not reconcile source evidence; no unsupported synthesis was substituted."
            logger.exception("Evidence synthesis failed", extra={"task_id": task.id, "error_type": type(exc).__name__})
        updated = task.model_copy(update={"status": status, "error": error})
        output = {"current_task": updated, "plan": [updated], "logs": [
            f"[synthesis_agent] Reconciliation {status}; {reducer.calls} bounded LLM calls."],
            "result_storage": [{"task_id": task.id, "node": self.name, "result": result,
                                "status": status, "error": error}],
        }
        if settings.ENABLE_EXECUTION_BENCHMARK_METRICS:
            output["llm_call_metrics"] = reducer.metrics
            output["execution_timings"] = [ExecutionTiming(operation="llm", phase="execute",
                name="evidence_reducer", agent_name=self.name, task_id=task.id,
                call_id=metric["call_id"], duration_ms=metric["request_latency_ms"], status=metric["status"],
                model=metric.get("model"), started_at=metric["started_at"], completed_at=metric["completed_at"]
            ).model_dump(mode="json") for metric in reducer.metrics]
            output["task_execution_metrics"] = [TaskExecutionMetric(task_id=task.id, node=self.name, status=status,
                duration_ms=round((perf_counter() - started) * 1000, 3), started_at=started_at,
                completed_at=datetime.now(timezone.utc)).model_dump(mode="json")]
        if status == "failed":
            output["metadata"] = {**metadata, "last_error": {"error_id": str(uuid.uuid4()),
                "code": "evidence_synthesis_failed", "category": "execution", "retryable": False,
                "occurred_at": datetime.now(timezone.utc).isoformat()}}
        logger.info("Evidence synthesis finished", extra={"duration_ms": round((perf_counter() - started) * 1000, 3),
                                                          "status": status, "task_id": task.id})
        return output

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
            warnings.append(f"{outcome['tool_name']}: source retrieval failed.")

        for outcome in partial_outcomes:
            warnings.append(f"{outcome['tool_name']}: some sources returned partial results.")

        return list(dict.fromkeys(warnings))
