"""Semantic validation for the current JSON workflow definition."""

from typing import Dict, Iterable, Set

from app.execution.state import FlowDefinition, Task
from app.shared.errors import ValidationError


def validate_workflow_definition(definition: FlowDefinition | None) -> None:
    """Validate task identity and DAG dependencies before persistence."""

    if definition is None:
        return

    tasks = definition.tasks
    task_ids = [task.id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValidationError("Workflow contains duplicate task IDs.")

    known_ids = set(task_ids)
    for task in tasks:
        missing = set(task.dependencies) - known_ids
        if missing:
            missing_values = ", ".join(str(value) for value in sorted(missing))
            raise ValidationError(
                f"Task {task.id} references unknown dependencies: {missing_values}."
            )

    _assert_acyclic(tasks)


def _assert_acyclic(tasks: Iterable[Task]) -> None:
    """Detect cycles with a small depth-first traversal."""

    graph: Dict[int, Set[int]] = {
        task.id: set(task.dependencies)
        for task in tasks
    }
    visiting: Set[int] = set()
    visited: Set[int] = set()

    def visit(task_id: int) -> None:
        if task_id in visiting:
            raise ValidationError("Workflow dependencies contain a cycle.")
        if task_id in visited:
            return

        visiting.add(task_id)
        for dependency_id in graph.get(task_id, set()):
            visit(dependency_id)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)
