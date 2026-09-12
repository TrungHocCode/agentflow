"""
Checkpointer Factory for LangGraph State Persistence.

Cung cấp singleton checkpointer instance dùng chung toàn bộ ứng dụng.
Hiện tại dùng MemorySaver (in-memory). Để swap sang PostgresSaver cho
production, chỉ cần thay đổi hàm get_checkpointer().
"""
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_checkpointer: Optional[MemorySaver] = None


def get_checkpointer() -> MemorySaver:
    """
    Trả về singleton MemorySaver instance với JsonPlusSerializer
    đã đăng ký Task type để tránh warning Msgpack deserialization.
    """
    global _checkpointer
    if _checkpointer is None:
        serde = JsonPlusSerializer(allowed_msgpack_modules=[("app.execution.state", "Task")])
        _checkpointer = MemorySaver(serde=serde)
    return _checkpointer


def reset_checkpointer() -> None:
    """
    Reset singleton checkpointer — dùng trong tests để isolation.
    """
    global _checkpointer
    _checkpointer = None
