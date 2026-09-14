"""Safe local filesystem storage for reports and charts."""

import hashlib
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.modules.results.models import ArtifactRecord


class LocalArtifactStorage:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.ARTIFACT_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def ingest_file(
        self,
        source_path: str,
        user_id: str,
        run_id: str,
        task_execution_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> ArtifactRecord | None:
        source = Path(source_path).resolve()
        if not source.is_file():
            return None
        artifact_id = os.urandom(16).hex()
        safe_name = self._safe_name(name or source.name)
        target_dir = self.root / "runs" / self._safe_name(user_id) / self._safe_name(run_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{artifact_id}-{safe_name}"
        shutil.copyfile(source, target)
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        relative_uri = target.relative_to(self.root).as_posix()
        return ArtifactRecord(
            id=artifact_id,
            user_id=user_id,
            run_id=run_id,
            task_execution_id=task_execution_id,
            name=safe_name,
            content_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            storage_uri=relative_uri,
            size_bytes=target.stat().st_size,
            checksum=checksum,
        )

    def resolve(self, storage_uri: str) -> Path:
        candidate = (self.root / storage_uri).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Artifact path escapes configured storage root.")
        return candidate

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(value).name)
        return cleaned[:180] or "artifact"
