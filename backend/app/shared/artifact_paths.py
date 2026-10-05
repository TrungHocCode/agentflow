"""Configured filesystem boundary shared by artifact producers and storage adapters."""

from pathlib import Path
from typing import Literal

from app.core.config import settings


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


def generated_file(category: Literal["reports", "charts"], filename: str) -> Path:
    """Resolve a producer filename without following a file link outside its directory."""
    directory = generated_directory(category)
    candidate = (directory / filename).resolve()
    if candidate.parent != directory:
        raise ValueError("Generated artifact file escapes its output directory.")
    return candidate
