"""
Checkpointer Factory for LangGraph State Persistence.

Cung cấp singleton checkpointer instance dùng chung toàn bộ ứng dụng.
Hiện tại dùng MemorySaver (in-memory). Để swap sang PostgresSaver cho
production, chỉ cần thay đổi hàm get_checkpointer().
"""
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

_checkpointer: Optional[MemorySaver] = None


def get_checkpointer() -> MemorySaver:
    """
    Trả về singleton MemorySaver instance.

    MemorySaver lưu toàn bộ checkpoint trong RAM — state sẽ mất khi
    server restart. Phù hợp cho development và testing.

    Để dùng PostgresSaver cho production:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(settings.POSTGRES_URL)
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer


def reset_checkpointer() -> None:
    """
    Reset singleton checkpointer — dùng trong tests để isolation.
    """
    global _checkpointer
    _checkpointer = None
