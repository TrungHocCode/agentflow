"""Filesystem blob store for immutable snapshot captured/normalized text."""

import re
from pathlib import Path

from app.modules.competitive_intelligence.snapshot_contracts import MAX_SPAN_CHARS
from app.shared.artifact_paths import artifact_root

MAX_BLOB_CHARS = 4_000_000


class SnapshotFileStore:
    """Run-scoped immutable blobs; Postgres holds URIs/hashes, never the text itself."""

    def __init__(self, root: str | None = None) -> None:
        self.root = artifact_root(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(value).name)
        return cleaned[:180] or "snapshot"

    def put(self, owner_id: str, run_id: str, snapshot_id: str, name: str, text: str) -> str:
        if len(text) > MAX_BLOB_CHARS:
            raise ValueError("Snapshot text exceeds the blob size bound.")
        target = (self.root / "runs" / self._safe(owner_id) / self._safe(run_id) / "snapshots"
                  / f"{self._safe(snapshot_id)}-{self._safe(name)}.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Snapshot blobs are immutable and must not be overwritten.")
        # newline="\n" keeps stored bytes identical on every OS so recorded hashes verify anywhere.
        target.write_text(text, encoding="utf-8", newline="\n")
        return target.relative_to(self.root).as_posix()

    def read_span(self, storage_uri: str, start: int, limit: int) -> str:
        candidate = (self.root / storage_uri).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Snapshot path escapes configured storage root.")
        start = max(0, start)
        limit = min(max(0, limit), MAX_SPAN_CHARS)
        text = candidate.read_text(encoding="utf-8")
        return text[start:start + limit]

    def read_full(self, storage_uri: str) -> str:
        return self.read_span(storage_uri, 0, MAX_BLOB_CHARS)
