"""Filesystem blob store for immutable rendered brief Markdown files."""

import re
from pathlib import Path

from app.modules.competitive_intelligence.snapshot_contracts import MAX_SPAN_CHARS
from app.shared.artifact_paths import artifact_root


class BriefFileStore:
    """Run-scoped immutable briefs; Postgres holds URIs/hashes, never the text itself."""

    def __init__(self, root: str | None = None) -> None:
        self.root = artifact_root(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(value).name)
        return cleaned[:180] or "brief"

    def put(self, owner_id: str, run_id: str, brief_id: str, text: str) -> str:
        target = (self.root / "runs" / self._safe(owner_id) / self._safe(run_id) / "briefs"
                  / f"{self._safe(brief_id)}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Brief files are immutable and must not be overwritten.")
        # newline="\n" keeps stored bytes identical on every OS so recorded hashes verify anywhere.
        target.write_text(text, encoding="utf-8", newline="\n")
        return target.relative_to(self.root).as_posix()

    def resolve(self, storage_uri: str) -> Path:
        candidate = (self.root / storage_uri).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Brief path escapes configured storage root.")
        return candidate

    def read_span(self, storage_uri: str, start: int, limit: int) -> str:
        text = self.resolve(storage_uri).read_text(encoding="utf-8")
        return text[max(0, start):max(0, start) + min(max(0, limit), MAX_SPAN_CHARS)]
