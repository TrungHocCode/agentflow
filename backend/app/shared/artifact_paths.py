"""Configured filesystem boundary shared by artifact producers and storage adapters."""

from pathlib import Path
from typing import Literal
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from uuid import uuid4
from collections.abc import Iterator

from app.core.config import settings


_RUN_SCOPE: ContextVar[str] = ContextVar("artifact_run_scope", default="standalone")


@contextmanager
def artifact_scope(run_id: str) -> Iterator[None]:
    """Scope producer staging without trusting an LLM filename or exposing account identifiers."""
    token = _RUN_SCOPE.set(sha256(run_id.encode("utf-8")).hexdigest())
    try:
        yield
    finally:
        _RUN_SCOPE.reset(token)


def artifact_root(root: str | None = None) -> Path:
    """Resolve relative roots against the process working directory."""
    return Path(root if root is not None else settings.ARTIFACT_ROOT).resolve()


def generated_directory(category: Literal["reports", "charts"], root: str | None = None) -> Path:
    """Reject redirected output directories before a producer writes any content."""
    boundary = artifact_root(root)
    directory = (boundary / category).resolve()
    if directory.parent != boundary:
        raise ValueError("Generated artifact directory escapes configured storage root.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def is_generated_artifact(source_path: str, root: str | None = None) -> bool:
    """Accept only existing report/chart files within the configured boundary."""
    try:
        boundary = artifact_root(root)
        candidate = Path(source_path).resolve()
        if not candidate.is_file():
            return False
        return any(
            directory.parent == boundary and directory in candidate.parents
            for directory in ((boundary / "reports").resolve(), (boundary / "charts").resolve())
        )
    except (OSError, RuntimeError, ValueError):
        return False


def generated_file(category: Literal["reports", "charts"], filename: str, generation_id: str | None = None) -> Path:
    """Resolve a producer filename without following a file link outside its directory."""
    boundary = generated_directory(category)
    generation = generation_id or uuid4().hex
    if not generation.isalnum():
        raise ValueError("Invalid artifact generation identifier.")
    directory = (boundary / _RUN_SCOPE.get() / generation).resolve()
    if boundary not in directory.parents:
        raise ValueError("Artifact staging escapes its output directory.")
    directory.mkdir(parents=True, exist_ok=True)
    candidate = (directory / filename).resolve()
    if candidate.parent != directory:
        raise ValueError("Generated artifact file escapes its output directory.")
    return candidate
