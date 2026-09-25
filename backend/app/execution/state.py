from typing import List, Dict, TypedDict, Optional, Literal, Annotated, Any
from pydantic import BaseModel, Field, model_validator
from langchain_core.messages import BaseMessage

# Reducer functions
def add_messages(left, right):
    """Custom reducer: giữ lại 10 messages mới nhất"""
    if not isinstance(left, list):
        left = [left]
    if not isinstance(right, list):
        right = [right]

    return (left + right)[-10:]

def add_logs(left: list, right: list) -> list:
    if left is None:
        left = []
    if right is None:
        right = []
    return left + right

def add_results(left: list, right: list) -> list:
    if left is None:
        left = []
    if right is None:
        right = []
    return left + right

def update_plan(left: list, right: list | Dict[str, Any]) -> list:
    if left is None:
        left = []
    if right is None:
        right = []
    if isinstance(right, dict) and right.get("__replace__") is True:
        replacement = right.get("tasks")
        if not isinstance(replacement, list):
            raise ValueError("Plan replacement must contain a task list.")
        return replacement
    # Merge tasks by id
    left_map = {t.id: t for t in left}
    for t in right:
        left_map[t.id] = t
    return sorted(left_map.values(), key=lambda x: x.id)

# Log entry model for structured logging
class LogEntry(BaseModel):
    timestamp: str
    level: Literal["INFO", "WARNING", "ERROR", "DEBUG"] = "INFO"
    node: str
    run_id: Optional[str] = None
    message: str

# Task model with dependency and limit extensions
class Task(BaseModel):
    id: int
    node: str
    agent_id: Optional[str] = None
    capability: Optional[str] = None
    tool_names: List[str] = Field(default_factory=list)
    status: Literal["done", "partial", "pending", "running", "failed", "skipped"]
    error: Optional[str] = None
    description: str
    dependencies: List[int] = Field(default_factory=list)
    timeout_seconds: Optional[int] = None
    max_iterations: Optional[int] = None

class FlowDefinition(BaseModel):
    flow_id: str
    name: str
    tasks: List[Task]
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentInfo(BaseModel):
    id: int
    name: str
    tool_names: list[str]

class State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage | str], add_messages]
    plan: Annotated[list[Task], update_plan]
    current_task: Optional[Task]
    logs: Annotated[list[Any], add_logs]
    result_storage: Annotated[list, add_results]
    execution_timings: Annotated[list[Dict[str, Any]], add_results]
    mode: Literal["conversation", "executing"]
    metadata: Optional[Dict[str, Any]]

class SupervisorOutput(BaseModel):
    """Validated decision contract for one Supervisor conversation turn."""

    decision: Literal["clarify", "propose_plan", "answer"]
    mode: Literal["conversation", "executing"] = "conversation"
    assistant_message: str = Field(min_length=1, max_length=4000)
    plan: List[Task] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "SupervisorOutput":
        if not self.assistant_message.strip():
            raise ValueError("Supervisor assistant_message must not be blank.")

        if self.decision == "propose_plan" and not self.plan:
            raise ValueError("A proposed workflow must contain at least one task.")
        if self.decision != "propose_plan" and self.plan:
            raise ValueError("Only a plan proposal may contain workflow tasks.")

        if not self.plan:
            return self

        task_ids = [task.id for task in self.plan]
        if any(task_id <= 0 for task_id in task_ids):
            raise ValueError("Task IDs must be positive integers.")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique within a plan.")

        known_ids = set(task_ids)
        dependencies: Dict[int, List[int]] = {}
        for task in self.plan:
            if not task.description.strip():
                raise ValueError(f"Task {task.id} must have a description.")
            if task.status != "pending":
                raise ValueError("New workflow tasks must have pending status.")
            if len(task.dependencies) != len(set(task.dependencies)):
                raise ValueError(f"Task {task.id} contains duplicate dependencies.")
            if any(dependency not in known_ids or dependency == task.id for dependency in task.dependencies):
                raise ValueError(f"Task {task.id} references an invalid dependency.")
            dependencies[task.id] = task.dependencies

        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(task_id: int) -> None:
            if task_id in visiting:
                raise ValueError("Workflow dependencies must not contain cycles.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)
        return self

class WorkerOutput(BaseModel):
    """Schema cho output của Worker Node"""
    status: Literal["done", "partial", "failed"]
    result: str
    error: Optional[str] = None
