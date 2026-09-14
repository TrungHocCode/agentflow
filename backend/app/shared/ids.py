"""Semantic ID aliases used in module contracts."""

from typing import NewType


DEFAULT_USER_ID = "default_user"

UserId = NewType("UserId", str)
WorkflowId = NewType("WorkflowId", str)
WorkflowVersionId = NewType("WorkflowVersionId", str)
RunId = NewType("RunId", str)
TaskId = NewType("TaskId", int)
EventId = NewType("EventId", str)
