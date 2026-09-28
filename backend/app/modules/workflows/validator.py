"""Shared semantic validation for workflow authoring and execution boundaries."""

import os
from typing import Any, Dict, Iterable, Set

from app.core.config import settings
from app.execution.state import FlowDefinition, Task
from app.modules.workflows.contract import normalize_workflow_definition
from app.modules.catalog.ports import CatalogRepository
from app.shared.errors import ValidationError


SUPPORTED_OUTPUT_TYPES = {
    "raw_data",
    "normalized_data",
    "summary",
    "comparison",
    "chart_spec",
    "report",
}


def validate_workflow_definition(
    definition: FlowDefinition | Dict[str, Any] | None,
    *,
    require_steps: bool = False,
) -> None:
    """Validate task identity, limits and DAG semantics for every workflow boundary."""

    if definition is None or definition == {}:
        return
    normalized = normalize_workflow_definition(definition)
    steps = normalized.get("steps", [])
    max_steps = int(getattr(settings, "MAX_WORKFLOW_STEPS", 50))
    if not steps and require_steps:
        raise ValidationError("Workflow must contain at least one step.")
    if len(steps) > max_steps:
        raise ValidationError(f"Workflow may contain at most {max_steps} steps.")

    keys = [str(step["task_key"]) for step in steps]
    if len(keys) != len(set(keys)):
        raise ValidationError("Workflow contains duplicate task keys.")
    known_keys = set(keys)
    graph: Dict[str, Set[str]] = {}
    max_timeout = int(getattr(settings, "MAX_TASK_TIMEOUT_SECONDS", 600))
    max_iterations = int(getattr(settings, "MAX_TASK_ITERATIONS", 5))

    for step in steps:
        key = str(step["task_key"])
        if not str(step.get("name") or "").strip():
            raise ValidationError(f"Step '{key}' must have a name.")
        if not str(step.get("description") or "").strip():
            raise ValidationError(f"Step '{key}' must have a description.")
        output_type = str(step.get("expected_output_type") or "raw_data")
        if output_type not in SUPPORTED_OUTPUT_TYPES:
            raise ValidationError(
                f"Step '{key}' has unsupported expected_output_type '{output_type}'."
            )
        dependencies = step.get("dependencies") or []
        if not isinstance(dependencies, list):
            raise ValidationError(f"Step '{key}' dependencies must be a list of task keys.")
        dependency_keys = [str(value) for value in dependencies]
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ValidationError(f"Step '{key}' contains duplicate dependencies.")
        missing = set(dependency_keys) - known_keys
        if missing:
            raise ValidationError(
                f"Step '{key}' references unknown dependencies: {', '.join(sorted(missing))}."
            )
        if key in dependency_keys:
            raise ValidationError(f"Step '{key}' cannot depend on itself.")

        config = step.get("config") or {}
        if not isinstance(config, dict):
            raise ValidationError(f"Step '{key}' config must be an object.")
        timeout_seconds = step.get("timeout_seconds", config.get("timeout_seconds"))
        if timeout_seconds is not None and (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
            or timeout_seconds > max_timeout
        ):
            raise ValidationError(
                f"Step '{key}' timeout_seconds must be between 1 and {max_timeout}."
            )
        iterations = step.get("max_iterations", config.get("max_iterations"))
        if iterations is not None and (
            not isinstance(iterations, int)
            or isinstance(iterations, bool)
            or iterations < 1
            or iterations > max_iterations
        ):
            raise ValidationError(
                f"Step '{key}' max_iterations must be between 1 and {max_iterations}."
            )
        input_mapping = step.get("input_mapping", step.get("input_mappings", {}))
        if input_mapping is not None and not isinstance(input_mapping, dict):
            raise ValidationError(f"Step '{key}' input_mapping must be an object.")
        graph[key] = set(dependency_keys)

    _assert_acyclic(graph)


async def validate_workflow_references(
    definition: Dict[str, Any],
    catalog: CatalogRepository | None,
) -> Dict[str, Any]:
    """Resolve active catalog IDs and enforce each agent's tool permission set."""

    if catalog is None or os.getenv("TESTING", "").lower() == "true":
        return definition
    normalized = normalize_workflow_definition(definition)
    agent_records = await catalog.list_agents(active_only=True)
    tool_records = await catalog.list_tools(active_only=True)
    agents = {value: agent for agent in agent_records for value in (agent.id, agent.name)}
    tools = {value: tool for tool in tool_records for value in (tool.id, tool.name)}

    for step in normalized.get("steps", []):
        task_key = str(step["task_key"])
        agent_ref = step.get("agent_id") or step.get("agent_name") or step.get("node")
        agent = agents.get(str(agent_ref)) if agent_ref is not None else None
        if agent is None:
            raise ValidationError(
                f"Step '{task_key}' must reference an active catalog agent by ID or name."
            )
        step["agent_id"] = agent.id
        allowed_names = set(agent.tool_names)
        configured_tool_names = (step.get("config") or {}).get("tool_names") or []
        explicit_tools = bool(
            step.get("tool_ids") or step.get("tool_names") or configured_tool_names
        )
        requested_refs = list(step.get("tool_ids") or [])
        requested_refs.extend(step.get("tool_names") or configured_tool_names)
        if not requested_refs:
            requested_refs = [
                name
                for name in agent.tool_names
                if name in tools and not _deployment_disabled(name)
            ]

        resolved_names: list[str] = []
        resolved_ids: list[str] = []
        for requested_ref in dict.fromkeys(str(value) for value in requested_refs):
            tool = tools.get(requested_ref)
            if tool is None:
                raise ValidationError(
                    f"Step '{task_key}' references an inactive or unknown tool '{requested_ref}'."
                )
            if tool.name not in allowed_names:
                raise ValidationError(
                    f"Tool '{tool.name}' is not authorized for agent '{agent.name}' on step '{task_key}'."
                )
            if explicit_tools and _deployment_disabled(tool.name):
                raise ValidationError(
                    f"Tool '{tool.name}' is disabled by deployment policy for step '{task_key}'."
                )
            resolved_names.append(tool.name)
            resolved_ids.append(tool.id)
        step["tool_names"] = resolved_names
        step["tool_ids"] = resolved_ids

    return normalize_workflow_definition(normalized)


def _deployment_disabled(tool_name: str) -> bool:
    if tool_name == "python_executor":
        return not settings.ENABLE_UNSANDBOXED_PYTHON_EXECUTION
    if tool_name == "email_sender":
        return not settings.ENABLE_EXTERNAL_SIDE_EFFECT_TOOLS
    return False


def _assert_acyclic(graph: Dict[str, Set[str]] | Iterable[Task]) -> None:
    """Detect dependency cycles using depth-first traversal."""

    if not isinstance(graph, dict):
        graph = {str(task.id): {str(value) for value in task.dependencies} for task in graph}
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(task_key: str) -> None:
        if task_key in visiting:
            raise ValidationError("Workflow dependencies contain a cycle.")
        if task_key in visited:
            return
        visiting.add(task_key)
        for dependency_key in graph.get(task_key, set()):
            visit(dependency_key)
        visiting.remove(task_key)
        visited.add(task_key)

    for task_key in graph:
        visit(task_key)
