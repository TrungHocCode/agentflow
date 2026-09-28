"""Lossless canonical workflow contracts and runtime-task projection."""

from copy import deepcopy
from typing import Any, Dict, List

from app.execution.state import FlowDefinition, Task
from app.shared.errors import ValidationError


def normalize_workflow_definition(
    definition: Dict[str, Any] | FlowDefinition | None,
) -> Dict[str, Any]:
    """Return a canonical snapshot plus a compatibility projection for execution.

    ``steps`` is the source of truth for authored workflow data. ``tasks`` is a
    deterministic runtime projection for the current executor. Keeping both
    prevents adapter conversions from discarding mappings, output contracts,
    stable keys, layout hints, or future step metadata.
    """

    if definition is None:
        return {}
    raw = definition.model_dump(mode="json") if isinstance(definition, FlowDefinition) else deepcopy(definition)
    if not isinstance(raw, dict):
        raise ValidationError("Workflow definition must be an object.")

    raw_steps = raw.get("steps")
    if raw_steps is None:
        raw_steps = _legacy_tasks_to_steps(raw.get("tasks"))
    if not isinstance(raw_steps, list):
        raise ValidationError("Workflow definition must contain a 'steps' or 'tasks' list.")

    steps: List[Dict[str, Any]] = []
    key_to_id: Dict[str, int] = {}
    for index, original in enumerate(raw_steps, start=1):
        if not isinstance(original, dict):
            raise ValidationError("Every workflow step must be an object.")
        step = deepcopy(original)
        task_key = str(step.get("task_key") or step.get("id") or f"step-{index}").strip()
        if not task_key:
            raise ValidationError(f"Step {index} must have a non-empty task_key.")
        if task_key in key_to_id:
            raise ValidationError(f"Workflow contains duplicate task key '{task_key}'.")
        key_to_id[task_key] = index
        step["task_key"] = task_key
        step.setdefault("name", step.get("node") or step.get("agent_name") or task_key)
        step.setdefault("description", step.get("description") or step["name"])
        step.setdefault("dependencies", [])
        step.setdefault("expected_output_type", "raw_data")
        step.setdefault("position", index - 1)
        step.setdefault("config", {})
        # Runtime status/error fields belong to TaskExecution, never a version.
        step.pop("status", None)
        step.pop("error", None)
        steps.append(step)

    tasks: List[Dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        dependencies: List[int] = []
        for dependency in step.get("dependencies", []):
            dependency_key = str(dependency)
            dependency_id = key_to_id.get(dependency_key)
            if dependency_id is None:
                try:
                    dependency_id = int(dependency)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        f"Step {step['task_key']} references unknown dependency '{dependency}'."
                    ) from exc
            dependencies.append(dependency_id)

        config = step.get("config") or {}
        if not isinstance(config, dict):
            raise ValidationError(f"Step {step['task_key']} config must be an object.")
        task = Task(
            id=index,
            task_key=step["task_key"],
            node=str(
                step.get("agent_id")
                or step.get("agent_name")
                or step.get("node")
                or "worker"
            ),
            agent_id=(
                str(step.get("agent_id"))
                if step.get("agent_id") is not None
                else None
            ),
            capability=(str(step["capability"]) if step.get("capability") is not None else None),
            tool_names=[
                str(value)
                for value in (step.get("tool_names") or config.get("tool_names") or [])
            ],
            tool_ids=[str(value) for value in (step.get("tool_ids") or [])],
            status="pending",
            description=str(step.get("description") or step.get("name") or step["task_key"]),
            dependencies=dependencies,
            timeout_seconds=step.get("timeout_seconds", config.get("timeout_seconds")),
            max_iterations=step.get("max_iterations", config.get("max_iterations")),
            expected_output_type=str(step.get("expected_output_type") or "raw_data"),
            input_mapping=deepcopy(
                step.get("input_mapping")
                or step.get("input_mappings")
                or config.get("input_mapping")
                or {}
            ),
            config=deepcopy(config),
        )
        tasks.append(task.model_dump(mode="json"))

    return {
        "flow_id": str(raw.get("flow_id") or raw.get("id") or "workflow"),
        "name": str(raw.get("name") or "Workflow"),
        "steps": steps,
        "tasks": tasks,
        "metadata": deepcopy(raw.get("metadata") or {}),
    }


def canonicalize_workflow_definition(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Expose the canonical source contract without regenerating/lossily mapping steps."""

    normalized = normalize_workflow_definition(definition)
    return {
        "steps": normalized.get("steps", []),
        "metadata": normalized.get("metadata", {}),
    }


def _legacy_tasks_to_steps(tasks: Any) -> List[Dict[str, Any]]:
    if not isinstance(tasks, list):
        raise ValidationError("Workflow definition must contain a 'steps' or 'tasks' list.")
    try:
        parsed = FlowDefinition.model_validate(
            {"flow_id": "workflow", "name": "Workflow", "tasks": tasks}
        )
    except Exception as exc:
        raise ValidationError(f"Legacy workflow tasks are invalid: {exc}") from exc

    id_to_key = {task.id: task.task_key or str(task.id) for task in parsed.tasks}
    steps: List[Dict[str, Any]] = []
    for index, task in enumerate(parsed.tasks):
        step: Dict[str, Any] = {
            "task_key": id_to_key[task.id],
            "name": task.node,
            "description": task.description,
            "agent_id": task.agent_id or task.node,
            "dependencies": [id_to_key.get(value, str(value)) for value in task.dependencies],
            "expected_output_type": task.expected_output_type,
            "position": index,
            "config": deepcopy(task.config),
        }
        if task.tool_names:
            step["tool_names"] = list(task.tool_names)
        if task.input_mapping:
            step["input_mapping"] = deepcopy(task.input_mapping)
        if task.timeout_seconds is not None:
            step["config"]["timeout_seconds"] = task.timeout_seconds
        if task.max_iterations is not None:
            step["config"]["max_iterations"] = task.max_iterations
        steps.append(step)
    return steps
