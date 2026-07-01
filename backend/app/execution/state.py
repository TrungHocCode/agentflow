from typing import List, Dict, TypedDict, Optional, Literal, Annotated
from pydantic import BaseModel
from langchain_core.messages import BaseMessage

#Reducer functions
def add_messages(left, right):
    """Custom reducer: giữ lại 10 messages mới nhất"""
    if not isinstance(left, list):
        left = [left]
    if not isinstance(right, list):
        right = [right]

    return (left + right)[-10:]

def add_logs(left: list, right: list) -> list:
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

#State
class Task(BaseModel):
    id: int
    node: str
    status: Literal["done", "pending", "running", "failed", "skipped"]
    error: Optional[str]
    description: str
class AgentInfo(BaseModel):
    id: int
    name: str
    tool_names: list[str]

class State(TypedDict): #Short-term mem? long-term mem ?
    messages: Annotated[list[BaseMessage | str], add_messages]
    plan: Annotated[list[Task], update_plan]
    current_task: Optional[Task]
    logs: Annotated[list[str], add_logs]
    result_storage: Annotated[list, add_results]  # lưu tạm kết quả worker, sẽ thay bằng DB sau
    mode: Literal["conversation", "executing"]
    direction: Optional[str]     # academic | application
    pending_review_content: Optional[str]  # Nội dung chờ human review trước khi post
    review_status: Optional[Literal["approved", "rejected", "edited"]]  # Trạng thái review

class SupervisorOutput(BaseModel):
    """Schema cho output của Supervisor Node"""
    mode: Literal["conversation", "executing"]  
    assistant_message: Optional[str] = ""
    plan: Optional[List[Task]] = None
    direction: Optional[str] = ""

class WorkerOutput(BaseModel):
    """Schema cho output của Worker Node"""
    status: Literal["done", "failed"]
    result: str
    error: Optional[str] = None