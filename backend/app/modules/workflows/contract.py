"""Canonical workflow contract helpers.

The execution engine still consumes the original ``Task`` shape.  This module
keeps the public workflow contract stable while translating the API shape into
that internal representation until normalized step tables are introduced.
"""

from typing import Any, Dict, List

from app.execution.state import FlowDefinition, Task
from app.shared.errors import ValidationError


def normalize_workflow_definition(
    definition: Dict[str, Any] | FlowDefinition | None,
) -> Dict[str, Any]:
    """Convert canonical ``steps`` or legacy ``tasks`` into one internal shape."""

    if definition is None:
        return {}
    if isinstance(definition, FlowDefinition):
        return definition.model_dump(mode="json")

    raw = dict(definition)
    if "tasks" in raw:
        FlowDefinition.model_validate(raw)
        return raw

    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise ValidationError("Workflow definition must contain a 'steps' or 'tasks' list.")

    key_to_id: Dict[str, int] = {}
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValidationError("Every workflow step must be an object.")
        task_key = str(step.get("task_key") or step.get("id") or index)
        if task_key in key_to_id:
            raise ValidationError(f"Workflow contains duplicate task key '{task_key}'.")
        key_to_id[task_key] = index

    tasks: List[Dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        dependencies = []
        for dependency in step.get("dependencies", []):
            dependency_id = key_to_id.get(str(dependency))
            if dependency_id is None:
                try:
                    dependency_id = int(dependency)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        f"Step {step.get('task_key', index)} references unknown dependency '{dependency}'."
                    ) from exc
            dependencies.append(dependency_id)
        config = step.get("config") or {}
        node = str(
            step.get("node")
            or step.get("agent_name")
            or step.get("agent_id")
            or "worker"
        )
        tasks.append(
            Task(
                id=index,
                node=node,
                agent_id=(
                    str(step["agent_id"])
                    if step.get("agent_id") is not None
                    else None
                ),
                capability=(
                    str(step["capability"])
                    if step.get("capability") is not None
                    else None
                ),
                tool_names=[
                    str(tool_name)
                    for tool_name in (
                        step.get("tool_names")
                        or config.get("tool_names")
                        or []
                    )
                ],
                status=str(step.get("status") or "pending"),
                description=str(step.get("description") or step.get("name") or step.get("task_key") or index),
                dependencies=dependencies,
                timeout_seconds=step.get("timeout_seconds", config.get("timeout_seconds")),
                max_iterations=step.get("max_iterations", config.get("max_iterations")),
            ).model_dump(mode="json")
        )

    return {
        "flow_id": str(raw.get("flow_id") or raw.get("id") or "workflow"),
        "name": str(raw.get("name") or "Workflow"),
        "tasks": tasks,
        "metadata": raw.get("metadata") or {},
    }


def canonicalize_workflow_definition(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Expose a stable ``steps`` view while retaining legacy compatibility fields."""

    normalized = normalize_workflow_definition(definition)
    steps = []
    for task in normalized.get("tasks", []):
        steps.append(
            {
                "task_key": str(task["id"]),
                "name": task.get("node", f"Task {task['id']}"),
                "description": task.get("description", ""),
                "agent_id": task.get("agent_id") or task.get("node"),
                "capability": task.get("capability"),
                "tool_names": task.get("tool_names", []),
                "dependencies": [str(value) for value in task.get("dependencies", [])],
                "config": {
                    key: task[key]
                    for key in ("timeout_seconds", "max_iterations")
                    if task.get(key) is not None
                },
                "expected_output_type": "raw_data",
                "position": task["id"],
            }
        )
    return {
        "steps": steps,
        "metadata": normalized.get("metadata", {}),
        "tasks": normalized.get("tasks", []),
    }
