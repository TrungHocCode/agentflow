"""Application-facing research output models."""

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


ResultType = Literal[
    "raw_data",
    "normalized_data",
    "summary",
    "comparison",
    "chart_spec",
    "report",
]


class ResultRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    task_execution_id: Optional[str] = None
    task_id: Optional[str] = None
    result_type: ResultType = "raw_data"
    content: Any = None
    schema_version: str = "1"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvidenceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    task_execution_id: Optional[str] = None
    source_url: str
    source_title: Optional[str] = None
    source_type: str = "other"
    excerpt: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=datetime.utcnow)


class ArtifactRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    run_id: str
    task_execution_id: Optional[str] = None
    name: str
    content_type: str = "application/octet-stream"
    storage_uri: str
    size_bytes: int = 0
    checksum: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ArtifactResponse(ArtifactRecord):
    """Safe API projection with a relative download URL."""

    download_url: Optional[str] = None
