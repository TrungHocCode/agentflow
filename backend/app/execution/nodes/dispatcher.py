from typing import Dict, Any, Optional
from app.execution.state import State, Task

class TaskDispatcher:
    """
    TaskDispatcher (ExecutionManager) manages the deterministic Run Phase execution.
    It inspects the execution plan, identifies runnable tasks based on dependency completion,
    and updates the State with the next task to be executed by a WorkerAgent.
    """
    async def dispatch(self, state: State) -> Dict[str, Any]:
        plan = state.get("plan") or []
        if not plan:
            return {"current_task": None, "logs": ["[TaskDispatcher] Plan is empty."]}

        completed_task_ids = {t.id for t in plan if t.status in {"done", "partial"}}
        failed_task_ids = {t.id for t in plan if t.status in {"failed", "skipped"}}

        # Propagate a failed prerequisite through the whole dependency chain in
        # one dispatch pass. Without this, a single failed task could leave
        # grandchildren in ``pending`` when the graph reaches END.
        skipped_tasks: list[Task] = []
        known_failed_ids = set(failed_task_ids)
        while True:
            newly_skipped = [
                task
                for task in plan
                if task.status == "pending"
                and task.id not in known_failed_ids
                and any(dep_id in known_failed_ids for dep_id in task.dependencies)
            ]
            if not newly_skipped:
                break
            for task in newly_skipped:
                skipped_task = task.model_copy(update={
                    "status": "skipped",
                    "error": "Skipped due to failed dependency."
                })
                skipped_tasks.append(skipped_task)
                known_failed_ids.add(task.id)

        if skipped_tasks:
            return {
                "plan": skipped_tasks,
                "current_task": None,
                "logs": [
                    f"[TaskDispatcher] Task {task.id} ('{task.description}') skipped due to dependency failure."
                    for task in skipped_tasks
                ],
            }

        # Find first runnable pending task whose dependencies are satisfied
        runnable_task: Optional[Task] = None
        for task in plan:
            if task.status == "pending":
                # Check dependencies
                deps_met = all(dep_id in completed_task_ids for dep_id in task.dependencies)
                if deps_met:
                    runnable_task = task
                    break

        if runnable_task:
            next_task = runnable_task.model_copy(update={"status": "running"})
            return {
                "current_task": next_task,
                "plan": [next_task],
                "logs": [f"[TaskDispatcher] Dispatching Task {next_task.id} ('{next_task.description}') to node '{next_task.node}'."]
            }

        # Check if all tasks are finished
        all_finished = all(t.status in ("done", "partial", "failed", "skipped") for t in plan)
        if all_finished:
            return {
                "current_task": None,
                "mode": "conversation",
                "logs": ["[TaskDispatcher] All tasks in plan finished execution."]
            }

        return {
            "current_task": None,
            "logs": ["[TaskDispatcher] Waiting for pending dependencies or blocked execution."]
        }
