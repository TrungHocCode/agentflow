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

        completed_task_ids = {t.id for t in plan if t.status == "done"}
        failed_task_ids = {t.id for t in plan if t.status == "failed"}

        # Find first runnable pending task whose dependencies are satisfied
        runnable_task: Optional[Task] = None
        for task in plan:
            if task.status == "pending":
                # Check dependencies
                deps_met = all(dep_id in completed_task_ids for dep_id in task.dependencies)
                deps_failed = any(dep_id in failed_task_ids for dep_id in task.dependencies)
                
                if deps_failed:
                    # Skip task if prerequisite failed
                    skipped_task = task.model_copy(update={
                        "status": "skipped",
                        "error": "Skipped due to failed dependency."
                    })
                    return {
                        "plan": [skipped_task],
                        "logs": [f"[TaskDispatcher] Task {task.id} ('{task.description}') skipped due to dependency failure."]
                    }
                
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
        all_finished = all(t.status in ("done", "failed", "skipped") for t in plan)
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
