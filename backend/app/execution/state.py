from typing import List, Dict, TypedDict, Optional, Literal, Annotated, Any
from pydantic import BaseModel, Field
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

def update_plan(left: list, right: list) -> list:
    if left is None:
        left = []
    if right is None:
        right = []
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
    status: Literal["done", "pending", "running", "failed", "skipped"]
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
    mode: Literal["conversation", "executing"]
    metadata: Optional[Dict[str, Any]]

class SupervisorOutput(BaseModel):
    """Schema cho output của Supervisor Node trong Build Phase"""
    mode: Literal["conversation", "executing"]  
    assistant_message: Optional[str] = ""
    plan: Optional[List[Task]] = None
    metadata: Optional[Dict[str, Any]] = None

class WorkerOutput(BaseModel):
    """Schema cho output của Worker Node"""
    status: Literal["done", "failed"]
    result: str
    error: Optional[str] = None